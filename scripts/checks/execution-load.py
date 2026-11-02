#!/usr/bin/env python3
"""Tier 3 load run for durable execution (Task 13): the planning-target workload.

Test-only. Starts its own disposable `postgres:18.6` container on a private
127.0.0.1 port (never 5432 or 55439, never a development database), then the API
(`--api-workers` processes on one socket), two orchestrators, a sweeper and synthetic
runtimes from the conformance harness, and admits through the public API:

- `--rate` admissions a second for `--minutes`, twice that for `--burst-minutes`,
  then the base rate again for `--recovery-minutes`, spread over `--environments`
  environments. A case reads twice, each read with log-normal latency (mean 1 s, p95
  3 s); 20 % of cases instead wait on a form that stays open.
- then `--waiting` executions into form waits; every worker and runtime is killed
  and restarted, and the time to resume, the claim latency after it, the sweep
  duration and the worker memory are recorded.

Writes `load-report.json` (hardware, PostgreSQL version and settings, latency
distributions, queue age, backlog, lock waits and hold times on
`execution_scheduler_cursors`, lock waiters by relation, provider lookups that
waited for their slot, reservations, deliveries and claims per phase, deadlocks,
database CPU, WAL rate, table and index sizes, dead-tuple ratios and the pass
criteria). For option C it also records every fair gate request by caller, gate
waiters by role, MultiXact growth and its margin, the sweeper's time per
reservation, and a revocation probe every few seconds: a revocation's locks under
the load, then a rollback, whose wait p95 and max are a pass criterion. A result is evidence about this harness on this hardware, not hosted
capacity or a service level. Needs Linux (runtime private state) and a Docker CLI.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import random
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/tests/execution/conformance"))
import driver as drivers
import world as worlds
from harness import (
    CALL_LOG,
    CURSOR_LOG,
    CYCLE_LOG,
    GATE_LOG,
    LOOKUP_LOG,
    PROFILE_DIRECTORY,
)
from processes import Processes

IMAGE = "postgres:18.6"
DATA = "/var/lib/postgresql/18/docker"
LABEL = "com.lotuspond.conformance=load"
REFUSED_PORTS = {5432, 55439}
# exp(N(mu, sigma)) seconds: mean 1 s, p95 3 s; capped inside the read's 30 s bound.
LATENCY = {"mu": -0.4348, "sigma": 0.9325, "cap_s": 20}
FORM_SHARE = 0.2
# Every runtime enrolls from this one host. The API allows a peer 20 enrollment
# exchanges a minute, and the harness's reruns of a refused enrollment count too.
ENROLLMENTS_PER_MINUTE = 15
# Runtime 0.1.0 ends its process (exit 3) on a refused or slow control call and
# leaves restarting to its service manager. The load stands in for one: an exited
# runtime starts again after this many seconds, and every restart is recorded.
RESTART_S = 5
SETTINGS = (
    "shared_buffers",
    "work_mem",
    "max_connections",
    "synchronous_commit",
    "fsync",
    "wal_level",
    "checkpoint_timeout",
    "max_wal_size",
    "effective_cache_size",
    "autovacuum",
    "deadlock_timeout",
    "log_lock_waits",
    "autovacuum_multixact_freeze_max_age",
)
CURSOR = "execution_scheduler_cursors"
# The owner's tier 3 bound for a revocation's wait at the planning target (option C).
REVOCATION_P95_S, REVOCATION_MAX_S = 1.0, 5.0
PROBE_SECONDS = 5
PROBE_TIMEOUT = "10s"
# `authority_gate`'s namespaces, as pg_locks shows them (classid).
GATE_NAMES = {0x4C505701: "workspace", 0x4C505503: "user", 0x4C505002: "pool"}


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(p / 100 * len(ordered)) - 1))]


def distribution(values):
    return {
        "count": len(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values) if values else None,
    }


def slope(points):
    """Least-squares change per second of (seconds, value) points."""
    if len(points) < 2:
        return None
    mean_x = sum(x for x, _ in points) / len(points)
    mean_y = sum(y for _, y in points) / len(points)
    spread = sum((x - mean_x) ** 2 for x, _ in points)
    return (
        sum((x - mean_x) * (y - mean_y) for x, y in points) / spread if spread else None
    )


def docker(*args, check=True, timeout=120):
    return subprocess.run(
        ["docker", *args], capture_output=True, timeout=timeout, check=check
    )


class Cluster:
    """The run's own PostgreSQL; removed, and the removal confirmed, at the end."""

    def __init__(self):
        self.name = "lotuspond-load-" + uuid4().hex[:12]
        for _ in range(100):
            port = random.randint(20000, 60000)
            with socket.socket() as probe:
                if (
                    port not in REFUSED_PORTS
                    and probe.connect_ex(("127.0.0.1", port)) != 0
                ):
                    self.port = port
                    break
        else:
            raise RuntimeError("no private port")
        self.url = f"postgresql+psycopg://lotuspond@127.0.0.1:{self.port}/lotuspond_platform_test"

    def start(self):
        docker(
            "run", "--detach", "--name", self.name, "--label", LABEL,
            "--publish", f"127.0.0.1:{self.port}:5432",
            "--env", "POSTGRES_USER=lotuspond", "--env", "POSTGRES_DB=lotuspond_platform_test",
            "--env", "POSTGRES_HOST_AUTH_METHOD=trust", "--env", f"PGDATA={DATA}",
            IMAGE, "-c", "max_connections=300", "-c", "log_lock_waits=on",
            "-c", "deadlock_timeout=100ms",
        )  # fmt: skip
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            ready = docker(
                "exec",
                self.name,
                "pg_isready",
                "-h",
                "127.0.0.1",
                "-U",
                "lotuspond",
                check=False,
            )
            with socket.socket() as probe:
                if (
                    ready.returncode == 0
                    and probe.connect_ex(("127.0.0.1", self.port)) == 0
                ):
                    return
            time.sleep(0.5)
        raise RuntimeError("load cluster never became ready")

    def stats(self):
        out = docker(
            "stats", "--no-stream", "--format", "{{json .}}", self.name, check=False
        )
        try:
            raw = json.loads(out.stdout.decode())
        except ValueError:
            return {"cpu_percent": None, "memory": None}
        cpu = raw.get("CPUPerc", "").rstrip("%")
        return {
            "cpu_percent": float(cpu) if cpu else None,
            "memory": raw.get("MemUsage"),
        }

    def cursor_lock_waits_from_log(self):
        """Waits PostgreSQL logged (over `deadlock_timeout`) by statements on the cursor."""
        log = docker("logs", self.name, check=False, timeout=300).stderr.decode(
            errors="replace"
        )
        waited, entry = [], None
        for line in log.splitlines():
            found = re.search(r"acquired \w+ on .* after ([0-9.]+) ms", line)
            if found:
                entry = float(found.group(1))
            elif entry is not None and "STATEMENT:" in line:
                if CURSOR in line:
                    waited.append(entry)
                entry = None
        return distribution(waited)

    def deadlocks_from_log(self) -> int:
        """Deadlocks PostgreSQL detected and logged during the run."""
        log = docker("logs", self.name, check=False, timeout=300).stderr.decode(
            errors="replace"
        )
        return sum(1 for line in log.splitlines() if "deadlock detected" in line)

    def remove(self) -> bool:
        """Remove the container and its anonymous volumes; confirm both are gone.

        Its volumes carry no label, so they are named before removal and checked
        after it, never inferred gone.
        """
        mounts = docker(
            "inspect",
            "--format",
            "{{range .Mounts}}{{.Name}} {{end}}",
            self.name,
            check=False,
        )
        volumes = mounts.stdout.decode().split() if mounts.returncode == 0 else []
        docker("rm", "--force", "--volumes", self.name, check=False)
        left = docker(
            "ps", "--all", "--quiet", "--filter", f"name=^{self.name}$", check=False
        )
        kept = [
            volume
            for volume in volumes
            if docker("volume", "inspect", volume, check=False).returncode == 0
        ]
        return left.returncode == 0 and not left.stdout.strip() and not kept


# ------------------------------------------------------------------ observations


def database_now(db):
    from sqlalchemy import text

    return db.scalar(
        text("SELECT floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint")
    )


def sample(world, cluster):
    """Queues, lock waits, WAL position and states, in database time."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    with Session(world.engine) as db:
        now = database_now(db)
        due = db.scalars(
            text(
                "SELECT :now - runnable_at_ms FROM executions WHERE runnable_at_ms <= :now"
            ),
            {"now": now},
        ).all()
        # Attempts the scheduler has not reserved yet: queued for capacity.
        unscheduled = db.scalars(
            text(
                "SELECT :now - floor(extract(epoch FROM a.created_at) * 1000)::bigint "
                "FROM execution_attempts a WHERE a.state = 'reserved' AND NOT EXISTS ("
                "SELECT 1 FROM execution_usage_reservations r WHERE r.attempt_id = a.id)"
            ),
            {"now": now},
        ).all()
        waits = db.execute(
            text(
                "SELECT count(*), coalesce(max(extract(epoch FROM clock_timestamp() - "
                "query_start)), 0) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
                f"AND query ILIKE '%{CURSOR}%'"
            )
        ).one()
        lock_waits = db.scalar(
            text("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'")
        )
        # Who blocks the waiters of each relation: role, state and the statement it
        # runs (SQL text only; bound values are never in pg_stat_activity's query).
        blocking = db.execute(
            text(
                "SELECT coalesce(w.relation, t.relation)::regclass::text, "
                "CASE WHEN b.application_name LIKE 'lotuspond-%' THEN "
                "split_part(b.application_name, ':', 1) ELSE 'api' END, b.state, "
                "left(regexp_replace(b.query, '\\s+', ' ', 'g'), 90), "
                "count(DISTINCT w.pid) FROM pg_locks w "
                "LEFT JOIN pg_locks t ON t.pid = w.pid AND t.locktype = 'tuple' "
                "AND t.granted "
                "CROSS JOIN LATERAL unnest(pg_blocking_pids(w.pid)) AS blocking(pid) "
                "JOIN pg_stat_activity b ON b.pid = blocking.pid "
                "WHERE NOT w.granted GROUP BY 1, 2, 3, 4"
            )
        ).all()
        # Every waiting backend by the relation it waits on: a row lock waits on the
        # holder's transaction while holding that row's tuple lock, so its relation is
        # the tuple lock's. A queue that moved off the cursor shows up here.
        by_relation = db.execute(
            text(
                "SELECT coalesce(w.relation, t.relation)::regclass::text, "
                "count(DISTINCT w.pid), coalesce(max(extract(epoch FROM "
                "clock_timestamp() - w.waitstart)), 0) FROM pg_locks w "
                "LEFT JOIN pg_locks t ON t.pid = w.pid AND t.locktype = 'tuple' "
                "AND t.granted WHERE NOT w.granted GROUP BY 1"
            )
        ).all()
        # Who holds what the cursor's waiters wait for: the API (no application
        # name) or a worker, and whether it is running SQL or idle in its transaction.
        holders = db.execute(
            text(
                "SELECT CASE WHEN b.application_name LIKE 'lotuspond-%' THEN "
                "split_part(b.application_name, ':', 1) ELSE 'api' END, b.state, "
                "count(DISTINCT b.pid) FROM pg_stat_activity w "
                "CROSS JOIN LATERAL unnest(pg_blocking_pids(w.pid)) AS blocking(pid) "
                "JOIN pg_stat_activity b ON b.pid = blocking.pid "
                f"WHERE w.wait_event_type = 'Lock' AND w.query ILIKE '%{CURSOR}%' "
                "GROUP BY 1, 2"
            )
        ).all()
        # Admitted and not finished, and not waiting on a person: measured from admission,
        # so a deferral's back-off can never hide an execution that is not being served.
        unserved = db.scalars(
            text(
                "SELECT :now - admitted_at_ms FROM executions WHERE state IN ('pending', 'running')"
            ),
            {"now": now},
        ).all()
        wal = db.scalar(text("SELECT pg_current_wal_lsn() - '0/0'::pg_lsn"))
        # Shared row locks by more than one transaction make MultiXact IDs (option C).
        # The database's oldest MultiXact is the cluster's first until a vacuum freezes,
        # so its age is the count made so far; the margin is to forced freezing.
        multixacts = db.execute(
            text(
                "SELECT mxid_age(datminmxid), age(datfrozenxid) FROM pg_database "
                "WHERE datname = current_database()"
            )
        ).one()
        gate_waiters = db.execute(
            text(
                "SELECT l.classid::bigint, l.mode, CASE WHEN a.application_name "
                "LIKE 'lotuspond-%' THEN split_part(a.application_name, ':', 1) ELSE 'api' "
                "END, count(*), coalesce(max(extract(epoch FROM clock_timestamp() - "
                "l.waitstart)), 0) FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid "
                "WHERE l.locktype = 'advisory' AND l.objsubid = 2 AND NOT l.granted "
                "GROUP BY 1, 2, 3"
            )
        ).all()
        states = dict(
            db.execute(text("SELECT state, count(*) FROM executions GROUP BY 1")).all()
        )
    ages = [*due, *unscheduled]
    return {
        "at_ms": now,
        "due": len(due),
        "unscheduled_attempts": len(unscheduled),
        "runnable_ages_ms": ages,
        "backlog": len(unserved),
        "ages_ms": unserved,
        "oldest_age_ms": max(unserved) if unserved else 0,
        "cursor_lock_waiters": waits[0],
        "cursor_lock_wait_max_s": float(waits[1]),
        "lock_waiters": lock_waits,
        "lock_waiters_by_relation": {
            str(relation): {"waiters": n, "max_wait_s": float(longest)}
            for relation, n, longest in by_relation
        },
        "blocking_holders": [
            {
                "relation": str(rel),
                "holder": f"{who}/{state}",
                "query": query,
                "waiters": n,
            }
            for rel, who, state, query, n in blocking
        ],
        "cursor_holders": {f"{who}/{state}": n for who, state, n in holders},
        "multixact_age": int(multixacts[0]),
        "xid_age": int(multixacts[1]),
        "gate_waiters": [
            {
                "gate": GATE_NAMES.get(namespace, str(namespace)),
                "mode": mode,
                "role": who,
                "waiters": n,
                "max_wait_s": float(longest),
            }
            for namespace, mode, who, n, longest in gate_waiters
        ],
        "wal_bytes": int(wal),
        "states": states,
        "database": cluster.stats(),
    }


def table_health(world):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    with Session(world.engine) as db:
        rows = db.execute(
            text(
                "SELECT relname, pg_total_relation_size(relid), pg_indexes_size(relid), "
                "n_live_tup, n_dead_tup FROM pg_stat_user_tables WHERE schemaname = :schema "
                "AND relname LIKE 'execution%' ORDER BY 2 DESC"
            ),
            {"schema": world.config["schema"]},
        ).all()
        settings = dict(
            db.execute(
                text("SELECT name, setting FROM pg_settings WHERE name = ANY(:names)"),
                {"names": list(SETTINGS)},
            ).all()
        )
        version = db.scalar(text("SELECT version()"))
    tables = [
        {
            "table": name,
            "total_bytes": total,
            "index_bytes": index,
            "live_rows": live,
            "dead_ratio": round(dead / (live + dead), 4) if live + dead else 0.0,
        }
        for name, total, index, live, dead in rows
    ]
    return version, settings, tables


def latencies(world, since_ms=0):
    """Dispatch (reserved to delivered) and claim (delivered to claimed), from rows."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    with Session(world.engine) as db:
        rows = db.execute(
            text(
                "SELECT d.issued_at_ms - floor(extract(epoch FROM r.created_at) * 1000)::bigint, "
                "l.claimed_at_ms - d.issued_at_ms FROM execution_activity_deliveries d "
                "JOIN execution_usage_reservations r ON r.attempt_id = d.attempt_id "
                "LEFT JOIN execution_leases l ON l.delivery_id = d.id "
                "WHERE d.issued_at_ms >= :since"
            ),
            {"since": since_ms},
        ).all()
        claims = db.execute(
            text(
                "SELECT count(*), coalesce(max(n), 0) FROM (SELECT count(*) AS n "
                "FROM execution_leases GROUP BY attempt_id) AS per_attempt"
            )
        ).one()
    return (
        [row[0] for row in rows if row[0] is not None],
        [row[1] for row in rows if row[1] is not None],
        {"claimed_attempts": claims[0], "max_claims_per_attempt": claims[1]},
    )


def activity_by_phase(world, timeline):
    """Reservations, deliveries and claims inside each load phase, from rows.

    Reservations count compute slots taken (shared child reservations take none),
    in database time, with each count's rate per second over its phase.
    """
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    with Session(world.engine) as db:
        reserved = db.scalars(
            text(
                "SELECT floor(extract(epoch FROM r.created_at) * 1000)::bigint "
                "FROM execution_usage_reservations r WHERE NOT EXISTS (SELECT 1 FROM "
                "execution_requests q WHERE q.workspace_id = r.workspace_id AND "
                "q.request_key = 'capacity.shared:' || r.attempt_id)"
            )
        ).all()
        issued = db.scalars(
            text("SELECT issued_at_ms FROM execution_activity_deliveries")
        ).all()
        claimed = db.scalars(text("SELECT claimed_at_ms FROM execution_leases")).all()

    def within(times, window):
        return sum(1 for t in times if window["start_ms"] <= t < window["end_ms"])

    phases = {}
    for name, window in timeline.items():
        seconds = max((window["end_ms"] - window["start_ms"]) / 1000, 1e-9)
        counts = {
            "reservations": within(reserved, window),
            "deliveries": within(issued, window),
            "claims": within(claimed, window),
        }
        phases[name] = {
            **counts,
            "per_second": {key: round(n / seconds, 2) for key, n in counts.items()},
        }
    return phases


def cpu_seconds(pid):
    """User plus system CPU of one child, from /proc (Linux)."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    except (OSError, IndexError, ValueError):
        return None


def rss_kb(pid):
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except OSError:
        return None
    return None


def family(pid):
    """A process and every live process below it, from /proc (Linux)."""
    below = {}
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                parent = int((entry / "stat").read_text().rsplit(")", 1)[1].split()[1])
            except (OSError, IndexError, ValueError):
                continue
            below.setdefault(parent, []).append(int(entry.name))
    found, queue = [], [pid]
    while queue:
        found.append(queue.pop())
        queue.extend(below.get(found[-1], ()))
    return found


def by_role(processes, measure):
    """A measure summed over the live children of each role (api, orchestrator, ...).

    Each child counts with the processes below it: a multi-process API's workers.
    """
    totals = {}
    for name, process in processes.children.items():
        if process.poll() is None:
            values = [measure(pid) for pid in family(process.pid)]
            if values[0] is not None:
                role = name.split("-")[0]
                value = sum(v for v in values if v is not None)
                totals[role] = round(totals.get(role, 0) + value, 1)
    return totals


def api_cpu(processes):
    """CPU seconds of each serving API process: its forked workers, or the one."""
    api = processes.children.get("api")
    if api is None or api.poll() is not None:
        return {}
    serving = family(api.pid)
    return {str(pid): cpu_seconds(pid) for pid in serving[1:] or serving}


def hardware():
    info = {
        "machine": platform.machine(),
        "platform": platform.platform(),
        "cpus": os.cpu_count(),
        "python": platform.python_version(),
    }
    try:
        model = re.search(r"model name\s*:\s*(.+)", Path("/proc/cpuinfo").read_text())
        info["cpu_model"] = model.group(1) if model else "not reported by /proc/cpuinfo"
        total = re.search(r"MemTotal:\s*(\d+) kB", Path("/proc/meminfo").read_text())
        info["memory_kb"] = int(total.group(1)) if total else None
    except OSError:
        pass
    return info


# ------------------------------------------------------------------ the workload


def admit(customer, revision):
    """One admission as a client sees it: the driver's retry policy, timed."""
    headers = {**customer.headers, "idempotency-key": uuid4().hex}
    body = {"deployment_revision_id": revision, "input": None}
    started = time.monotonic()
    for tries in range(1, 21):
        response = customer.client.post(
            customer.prefix + "/executions", json=body, headers=headers
        )
        busy = (
            response.status_code == 503
            and drivers._code(response) == "authority_unavailable"
        )
        if not busy or tries == 20:
            break
        time.sleep(0.1)
    code = drivers._code(response) if response.status_code >= 400 else None
    return response.status_code, code, tries, time.monotonic() - started


def reads_then_form():
    """The plan's waiting case: two reads, then a human form that stays open."""
    package = drivers.two_reads()
    form = drivers.form_package()
    package["package_id"] = "two-reads-then-form"
    package["policies"] = form["policies"]
    package["body"]["steps"].append(form["body"]["steps"][0])
    package["body"]["result"] = form["body"]["result"]
    package["body"]["result_schema"] = form["body"]["result_schema"]
    package["output_schema"] = form["output_schema"]
    return package


class Load:
    def __init__(self, world, processes, cluster, args):
        self.world, self.processes, self.cluster, self.args = (
            world,
            processes,
            cluster,
            args,
        )
        self.operator = drivers.Operator(world, processes)
        self.tenants, self.runtimes = [], []
        self.admissions, self.samples, self.probes = [], [], []
        self.lock = threading.Lock()
        # The service-manager stand-in: running while `keeping` is set; it holds
        # `keeper_lock` while it restarts, so a planned kill never races it.
        self.restarts, self.keeping = [], threading.Event()
        self.keeper_lock, self.keeper_stop = threading.Lock(), threading.Event()
        self.keeper_thread = None
        self.phase = "setup"
        self.cursor_log = Path(os.environ[CURSOR_LOG])
        self.cycle_log = Path(os.environ[CYCLE_LOG])
        self.lookup_log = Path(os.environ[LOOKUP_LOG])
        self.call_log = Path(os.environ[CALL_LOG])

    def setup(self):
        operator = self.operator
        operator.demand(
            30000
        )  # within the read's 30 s bound; a lease never outlives it
        enrolled = []
        for n in range(self.args.environments):
            customer = operator.tenant(self.world.engine, label=f"Load {n}")
            reads = operator.deploy(
                customer,
                "two-reads",
                package=drivers.two_reads(),
                dependencies=[operator.documents[0]],
                capacity=5,
            )
            # The plan's case: two reads; 20 % then wait on a form that stays open.
            form = operator.deploy(
                customer,
                "reads-then-form",
                package=reads_then_form(),
                dependencies=[operator.documents[0], "waits.policy"],
                capacity=5,
            )
            self.tenants.append((customer, reads, form))
            for r in range(self.args.runtimes_per_environment):
                if len(enrolled) >= ENROLLMENTS_PER_MINUTE:
                    window = enrolled[-ENROLLMENTS_PER_MINUTE] + 61 - time.monotonic()
                    time.sleep(max(0.0, window))
                enrolled.append(time.monotonic())
                self.runtimes.append(
                    operator.runtime(
                        customer,
                        f"load-{n}-{r}",
                        read={"read": {"value": None, "lognormal": LATENCY}},
                    )
                )

    def keeper(self):
        exited = {}  # name -> (first seen exited, exit code)
        while not self.keeper_stop.wait(1.0):
            if not self.keeping.is_set():
                exited.clear()
                continue
            with self.keeper_lock:
                if not self.keeping.is_set():
                    continue
                for item in self.runtimes:
                    name = item["home"].name
                    process = self.processes.children.get(name)
                    if process is None or process.poll() is None:
                        continue
                    seen, code = exited.setdefault(
                        name, (time.monotonic(), process.returncode)
                    )
                    if time.monotonic() - seen < RESTART_S:
                        continue
                    try:
                        self.processes.start_runtime_process(name)
                    except AssertionError:  # its process group is not gone yet
                        continue
                    del exited[name]
                    with self.lock:
                        self.restarts.append(
                            {
                                "name": name,
                                "exit": code,
                                "phase": self.phase,
                                "at_ms": int(time.time() * 1000),
                            }
                        )

    def start_keeping(self):
        if self.keeper_thread is None:
            self.keeper_thread = threading.Thread(target=self.keeper, daemon=True)
            self.keeper_thread.start()
        self.keeping.set()

    def pause_keeping(self):
        self.keeping.clear()
        with self.keeper_lock:  # waits out a restart in progress
            pass

    def stop_keeping(self):
        self.pause_keeping()
        self.keeper_stop.set()
        if self.keeper_thread is not None:
            self.keeper_thread.join(timeout=30)

    def restart_summary(self):
        with self.lock:
            restarts = list(self.restarts)
        by_phase, codes = {}, {}
        for item in restarts:
            by_phase[item["phase"]] = by_phase.get(item["phase"], 0) + 1
            codes[str(item["exit"])] = codes.get(str(item["exit"]), 0) + 1
        return {
            "restart_after_s": RESTART_S,
            "total": len(restarts),
            "by_phase": by_phase,
            "exit_codes": codes,
            "runtimes_restarted": len({item["name"] for item in restarts}),
        }

    def revocations(self, stop):
        """Revocation probes (owner condition on option C): every `PROBE_SECONDS`, one
        tenant's revocation locks under the load, then a rollback. In turn: a workspace
        change (its gate exclusive, its row), an identity revocation (the workspace
        gate, the owner's user gate, the user row) and a runtime revocation (the
        workspace gate, the pool row). `s` runs from the first gate to the last row."""
        from sqlalchemy import select, text
        from sqlalchemy.orm import Session

        from lotuspond import authority_gate
        from lotuspond.models import Membership, RuntimePool, User, Workspace

        kinds, owners, n = ("workspace", "user", "pool"), {}, 0
        while not stop.wait(PROBE_SECONDS):
            customer = self.tenants[n % len(self.tenants)][0]
            entry = {"phase": self.phase, "kind": kinds[n % len(kinds)]}
            n += 1
            db = Session(self.world.engine)
            began = time.monotonic()
            try:
                db.begin()
                db.execute(text(f"SET LOCAL lock_timeout = '{PROBE_TIMEOUT}'"))
                if entry["kind"] == "user" and customer.workspace not in owners:
                    owners[customer.workspace] = db.scalar(
                        select(Membership.user_id).where(
                            Membership.workspace_id == customer.workspace,
                            Membership.role == "owner",
                        )
                    )
                began = time.monotonic()
                authority_gate.workspace(db, customer.workspace, exclusive=True)
                if entry["kind"] == "workspace":
                    row = select(Workspace).where(Workspace.id == customer.workspace)
                elif entry["kind"] == "user":
                    owner = owners[customer.workspace]
                    authority_gate.users(db, [owner], exclusive=True)
                    row = select(User).where(User.id == owner)
                else:
                    row = select(RuntimePool).where(RuntimePool.id == customer.pool_id)
                db.scalar(row.with_for_update())
                entry["s"] = round(time.monotonic() - began, 4)
            except Exception as error:  # noqa: BLE001 - a refused probe is recorded
                entry["s"] = round(time.monotonic() - began, 4)
                entry["error"] = type(error).__name__
            finally:
                db.rollback()
                db.close()
            with self.lock:
                self.probes.append(entry)

    def sampler(self, stop):
        while not stop.wait(self.args.sample_seconds):
            try:
                observed = sample(self.world, self.cluster)
            except Exception as error:  # noqa: BLE001 - a missed sample is recorded
                observed = {"error": type(error).__name__}
            observed["phase"] = self.phase
            with self.lock:
                self.samples.append(observed)

    def one(self, n, form_share, phase, arrived):
        """One admission, timed from its scheduled arrival (open loop) and as sent."""
        customer, reads, form = self.tenants[n % len(self.tenants)]
        revision = form if random.random() < form_share else reads
        status, code, tries, seconds = admit(customer, revision)
        with self.lock:
            self.admissions.append(
                {
                    "phase": phase,
                    "status": status,
                    "code": code,
                    "tries": tries,
                    "s": time.monotonic() - arrived,
                    "service_s": seconds,
                }
            )

    def drive(self):
        """Poisson arrivals: the base rate, a burst at twice it, the base rate again."""
        schedule = [
            ("steady", self.args.rate, self.args.minutes),
            ("burst", self.args.rate * 2, self.args.burst_minutes),
            ("recovery", self.args.rate, self.args.recovery_minutes),
        ]
        timeline, n = {}, 0
        pool, finished = ThreadPoolExecutor(64), False
        try:
            for name, rate, minutes in schedule:
                self.phase = name
                timeline[name] = {"start_ms": int(time.time() * 1000)}
                start_cpu = by_role(self.processes, cpu_seconds)
                start_api = api_cpu(self.processes)
                deadline = time.monotonic() + minutes * 60
                next_at = time.monotonic()
                while True:
                    next_at += random.expovariate(rate)
                    if next_at >= deadline:
                        break
                    time.sleep(max(0.0, next_at - time.monotonic()))
                    n += 1
                    pool.submit(self.one, n, FORM_SHARE, name, next_at)
                timeline[name]["end_ms"] = int(time.time() * 1000)
                end_cpu = by_role(self.processes, cpu_seconds)
                timeline[name]["process_cpu_s"] = {
                    role: round(end_cpu[role] - start_cpu.get(role, 0), 1)
                    for role in end_cpu
                }
                timeline[name]["api_cpu_s_by_process"] = {
                    pid: round(cpu - (start_api.get(pid) or 0), 1)
                    for pid, cpu in api_cpu(self.processes).items()
                    if cpu is not None
                }
            self.phase = "drain"
            finished = True
        finally:
            # Interrupted: queued admissions are dropped, never waited for.
            pool.shutdown(wait=finished, cancel_futures=not finished)
        return timeline

    def criteria(self, timeline, per_attempt, claims):
        good = [s for s in self.samples if "error" not in s]
        steady = [s for s in good if s["phase"] == "steady"]
        admitted = [a for a in self.admissions if a["phase"] == "steady"]
        accepted = [a["s"] for a in admitted if a["status"] == 201]
        # The plan's runnable queue (due executions and schedulable attempts), and the
        # stricter age from admission of everything not yet served: the larger counts.
        runnable = [age for s in steady for age in s["runnable_ages_ms"]]
        unserved = [age for s in steady for age in s["ages_ms"]]
        age_p95 = max(percentile(runnable, 95) or 0, percentile(unserved, 95) or 0)
        end = timeline["steady"]["end_ms"]
        final = [
            ((s["at_ms"] - end) / 1000, s["backlog"])
            for s in steady
            if s["at_ms"] >= end - 1800000
        ]
        rate = slope(final)
        mean_backlog = sum(b for _, b in final) / len(final) if final else 0
        growth = None if rate is None else rate * 1800
        band = (
            percentile([s["backlog"] for s in steady if s["at_ms"] >= end - 600000], 95)
            or 0
        )
        burst_end = timeline["burst"]["end_ms"]
        recovered = next(
            (
                (s["at_ms"] - burst_end) / 1000
                for s in good
                if s["at_ms"] >= burst_end and s["backlog"] <= max(band, 10)
            ),
            None,
        )
        return {
            "admission_p95_below_1s": {
                "observed_s": percentile(accepted, 95),
                "refused": len(admitted) - len(accepted),
                "passed": bool(accepted)
                and percentile(accepted, 95) < 1.0
                and len(accepted) == len(admitted),
            },
            "runnable_queue_age_p95_below_30s": {
                "observed_s": age_p95 / 1000,
                "runnable_p95_s": (percentile(runnable, 95) or 0) / 1000,
                "unserved_from_admission_p95_s": (percentile(unserved, 95) or 0) / 1000,
                "passed": bool(steady) and age_p95 < 30000,
            },
            "no_sustained_backlog_growth_final_30_min": {
                "observed_growth_over_30_min": growth,
                "mean_backlog": round(mean_backlog, 1),
                "passed": growth is not None
                and growth <= max(10.0, 0.1 * mean_backlog),
            },
            "burst_backlog_recovers_within_10_min": {
                "steady_band": band,
                "observed_s": recovered,
                "passed": recovered is not None and recovered <= 600,
            },
            "at_most_one_provider_call_per_attempt": {
                "provider_calls": sum(per_attempt.values()),
                "max_calls_per_attempt": max(per_attempt.values(), default=0),
                "unattributed_calls": per_attempt.get("unknown", 0),
                **claims,
                "passed": max(per_attempt.values(), default=0) <= 1
                and "unknown" not in per_attempt
                and claims["max_claims_per_attempt"] <= 1,
            },
            "revocation_wait_bounded": revocation_bound(self.probes),
        }

    def waiting(self):
        """`--waiting` executions in form waits; then kill and restart every worker."""
        from lotuspond.models import Execution
        from sqlalchemy import func, select
        from sqlalchemy.orm import Session

        args, processes, operator = self.args, self.processes, self.operator
        self.phase = "waiting"

        def count_waiting():
            with Session(self.world.engine) as db:
                return db.scalar(
                    select(func.count())
                    .select_from(Execution)
                    .where(Execution.state == "waiting")
                )

        before = count_waiting()
        started = time.monotonic()
        with ThreadPoolExecutor(32) as pool:
            list(
                pool.map(
                    lambda n: self.one(n, 1.0, "waiting", time.monotonic()),
                    range(args.waiting),
                )
            )
        admitted_s = time.monotonic() - started
        admitted = sum(
            1 for a in self.admissions if a["phase"] == "waiting" and a["status"] == 201
        )
        deadline = time.monotonic() + 1800
        while count_waiting() < before + admitted and time.monotonic() < deadline:
            time.sleep(5)
        settled_s = time.monotonic() - started
        reached = count_waiting() - before
        self.pause_keeping()  # the planned kill and restart below are not restarts
        memory_before = by_role(processes, rss_kb)
        killed_at = time.monotonic()
        for name, process in list(processes.children.items()):
            if process.poll() is None and name != "api":
                processes.kill(name)
        restart, restarted_at = time.monotonic(), time.time()
        names = ("orchestrator-r1", "orchestrator-r2", "sweeper-r1")
        for name in names:
            processes.start_worker(name.split("-")[0], name, wait=False)
        processes.wait_ready(*names, seconds=600)
        ready_s = time.monotonic() - restart
        for item in self.runtimes:
            processes.start_runtime_process(item["home"].name)
        self.start_keeping()
        since_ms = int(time.time() * 1000)
        # Resumed work with the whole waiting set present: new reads, and form waits
        # signalled.
        resumed, refused, probes_refused = [], [], []
        for n in range(20):
            customer, reads, _ = self.tenants[n % len(self.tenants)]
            try:
                execution_id = operator.start(customer, reads)["id"]
            except AssertionError as error:  # for example the pending cap: counted
                probes_refused.append(getattr(error, "code", None) or "refused")
                continue
            resumed.append((customer, execution_id, time.monotonic()))
        with Session(self.world.engine) as db:
            chosen = db.execute(
                select(Execution.id, Execution.environment_id)
                .where(Execution.state == "waiting")
                .limit(100)
            ).all()
        customers = {c.scope["environment_id"]: c for c, _, _ in self.tenants}
        for execution_id, environment_id in chosen:
            customer = customers[environment_id]
            try:
                wait = operator.open_wait(customer, execution_id, "review")
                operator.signal(
                    customer, execution_id, wait["id"], "form", {"approved": True}
                )
            except AssertionError as error:  # a refusal or a deadline: counted
                refused.append(getattr(error, "code", None) or "unsettled")
                continue
            resumed.append((customer, execution_id, time.monotonic()))
        # One shared bound for all of them, read from rows: state, reason and when.
        started_at = {execution_id: at for _, execution_id, at in resumed}
        finished, deadline = {}, time.monotonic() + 1800
        while len(finished) < len(started_at) and time.monotonic() < deadline:
            with Session(self.world.engine) as db:
                rows = db.execute(
                    select(Execution.id, Execution.state, Execution.reason).where(
                        Execution.id.in_(list(started_at))
                    )
                ).all()
            for execution_id, state, reason in rows:
                if execution_id not in finished and state in ("succeeded", "failed"):
                    elapsed = time.monotonic() - started_at[execution_id]
                    finished[execution_id] = (state, reason, elapsed)
            time.sleep(2)
        outcomes = {}
        for state, reason, _ in finished.values():
            key = f"{state}/{reason}"
            outcomes[key] = outcomes.get(key, 0) + 1
        _, claim, _ = latencies(self.world, since_ms=since_ms)
        return {
            "waiting_admitted": admitted,
            "waiting_reached_before_kill": reached,
            "waiting_total": count_waiting(),
            "admission_s": round(admitted_s, 1),
            "all_waiting_after_s": round(settled_s, 1),
            "kill_s": round(restart - killed_at, 2),
            "workers_ready_after_restart_s": round(ready_s, 1),
            "resumed": len(started_at),
            "probe_admissions_refused": sorted(set(probes_refused)),
            "probe_admissions_refused_count": len(probes_refused),
            "signals_refused": sorted(set(refused)),
            "signals_refused_count": len(refused),
            "resumed_unsettled_after_1800_s": len(started_at) - len(finished),
            "resumed_outcomes": outcomes,
            "resumed_completion_s": distribution([s for _, _, s in finished.values()]),
            "claim_latency_ms_after_restart": distribution(claim),
            # The restarted sweeper's own passes over the waiting set.
            "sweep_duration_s": distribution(
                [
                    item["s"]
                    for item in timing_log(self.cycle_log)
                    if item["cycle"] == "sweep" and item["at"] >= restarted_at
                ]
            ),
            "worker_rss_kb_before_kill": memory_before,
            "worker_rss_kb_after_restart": by_role(processes, rss_kb),
        }


def timing_log(path):
    """Lines of one test-only timing log (cursor waits or worker cycles)."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def cursor_waits(path):
    """Every scheduler-cursor acquisition, by process role, as each process timed it."""
    logged = timing_log(path)
    entries = [item for item in logged if "ms" in item]
    holds = [item for item in logged if "hold_ms" in item]
    roles = sorted({item["role"] for item in logged})
    return {
        "all_ms": distribution([item["ms"] for item in entries]),
        "over_100ms": sum(1 for item in entries if item["ms"] > 100),
        "ended_by_a_timeout_or_error": sum(1 for item in entries if item["failed"]),
        "by_role_ms": {
            role: distribution([item["ms"] for item in entries if item["role"] == role])
            for role in roles
        },
        # From the acquisition to the end of its transaction's commit or rollback.
        "hold_ms": distribution([item["hold_ms"] for item in holds]),
        "hold_by_role_ms": {
            role: distribution(
                [item["hold_ms"] for item in holds if item["role"] == role]
            )
            for role in roles
        },
        "holds_rolled_back": sum(1 for item in holds if item["failed"]),
    }


def lookup_waits(path):
    """Provider lookups that waited for their slot (finding 3), by slot and role."""
    entries = timing_log(path)
    keys = sorted({(item["slot"], item["role"]) for item in entries})
    return {
        "waiting_callers": len(entries),
        "wait_ms": distribution([item["ms"] for item in entries]),
        "refused_after_the_bound": sum(1 for item in entries if not item["acquired"]),
        "by_slot_and_role": {
            f"{slot}/{role}": {
                "waiting_callers": sum(
                    1 for i in entries if (i["slot"], i["role"]) == (slot, role)
                ),
                "wait_ms": distribution(
                    [i["ms"] for i in entries if (i["slot"], i["role"]) == (slot, role)]
                ),
            }
            for slot, role in keys
        },
    }


def service_calls(path, timeline):
    """Admission and runtime activity calls by phase: count, wall and thread CPU."""
    entries = timing_log(path)
    names = sorted({item["call"] for item in entries})

    def summary(items):
        return {
            "count": len(items),
            "failed": sum(1 for item in items if item["failed"]),
            "wall_ms": distribution([item["wall_ms"] for item in items]),
            "cpu_ms": distribution([item["cpu_ms"] for item in items]),
            "cpu_s_total": round(sum(item["cpu_ms"] for item in items) / 1000, 1),
        }

    return {
        phase: {
            name: summary(
                [
                    item
                    for item in entries
                    if item["call"] == name
                    and window["start_ms"] <= item["at"] < window["end_ms"]
                ]
            )
            for name in names
        }
        for phase, window in timeline.items()
    }


def relation_waits(samples):
    """Sampled lock waiters by the relation they waited on, across every sample."""
    relations = sorted(
        {name for s in samples for name in s.get("lock_waiters_by_relation", {})}
    )
    return {
        name: {
            "waiters": distribution(
                [
                    s.get("lock_waiters_by_relation", {})
                    .get(name, {})
                    .get("waiters", 0)
                    for s in samples
                ]
            ),
            "max_wait_s": distribution(
                [
                    s["lock_waiters_by_relation"][name]["max_wait_s"]
                    for s in samples
                    if name in s.get("lock_waiters_by_relation", {})
                ]
            ),
        }
        for name in relations
    }


def revocation_bound(probes):
    """The owner's bound on a revocation's wait under load: p95 ≤ 1 s and max ≤ 5 s,
    over every probe while admissions run (a refused probe counts as its timeout)."""
    loaded = [p for p in probes if p["phase"] in ("steady", "burst", "recovery")]
    waits = [p["s"] for p in loaded]
    return {
        "bound": {"p95_s": REVOCATION_P95_S, "max_s": REVOCATION_MAX_S},
        "wait_s": distribution(waits),
        "by_kind": {
            kind: distribution([p["s"] for p in loaded if p["kind"] == kind])
            for kind in sorted({p["kind"] for p in loaded})
        },
        "refused": sorted({p["error"] for p in loaded if "error" in p}),
        "passed": bool(waits)
        and not any("error" in p for p in loaded)
        and percentile(waits, 95) <= REVOCATION_P95_S
        and max(waits) <= REVOCATION_MAX_S,
    }


def gate_waits(path, timeline):
    """Fair gate requests by caller, gate and mode, per phase (`record_gate_waits`).

    `ms` is a request's wait plus one round trip; `waited` counts those over 10 ms.
    Refused `try_shared` claims are `skipped`."""
    entries = timing_log(path)
    result = {}
    for phase, window in timeline.items():
        groups = {}
        for item in entries:
            if window["start_ms"] <= item["at"] < window["end_ms"]:
                name = f"{item['gate']}/{item.get('mode', 'shared')} {item['caller']}"
                groups.setdefault(name, []).append(item)
        result[phase] = {
            name: {
                "requests": len([i for i in items if not i.get("skipped")]),
                "skipped": len([i for i in items if i.get("skipped")]),
                "waited_over_10ms": len([i for i in items if i.get("ms", 0) > 10]),
                "ms": distribution([i["ms"] for i in items if "ms" in i]),
                "total_s": round(sum(i.get("ms", 0) for i in items) / 1000, 1),
            }
            for name, items in sorted(groups.items())
        }
    return result


def sampled_gate_waiters(samples):
    """Backends waiting on a gate at each sample, by gate, mode and role."""
    names = sorted(
        {
            f"{w['gate']}/{w['mode']}/{w['role']}"
            for s in samples
            for w in s.get("gate_waiters", [])
        }
    )
    result = {}
    for name in names:
        rows = [
            [
                w
                for w in s.get("gate_waiters", [])
                if f"{w['gate']}/{w['mode']}/{w['role']}" == name
            ]
            for s in samples
        ]
        result[name] = {
            "waiters": distribution([sum(w["waiters"] for w in r) for r in rows]),
            "max_wait_s": distribution(
                [max(w["max_wait_s"] for w in r) for r in rows if r]
            ),
        }
    return result


def multixact_growth(samples, settings_rows):
    """MultiXact IDs made during the run, their rate, and the margin left before
    PostgreSQL forces a freeze (`autovacuum_multixact_freeze_max_age`) and before
    wraparound (2^31)."""
    points = [(s["at_ms"], s["multixact_age"]) for s in samples if "multixact_age" in s]
    if len(points) < 2:
        return {"samples": len(points)}
    (first_at, first), (last_at, last) = points[0], points[-1]
    freeze = int(
        settings_rows.get("autovacuum_multixact_freeze_max_age") or 400_000_000
    )
    rate = (
        (last - first) / ((last_at - first_at) / 1000) if last_at > first_at else None
    )
    return {
        "first_age": first,
        "last_age": last,
        "made": last - first,
        "per_second": round(rate, 2) if rate is not None else None,
        "freeze_max_age": freeze,
        "margin_to_forced_freeze": freeze - last,
        "days_to_forced_freeze_at_this_rate": round((freeze - last) / rate / 86400, 1)
        if rate
        else None,
        "margin_to_wraparound": 2**31 - last,
    }


def sweep_per_reservation(path):
    """The sweeper's time per attempt it reserved: each sweep that reserved any."""
    sweeps = [
        item
        for item in timing_log(path)
        if item["cycle"] == "sweep" and item.get("report", {}).get("scheduled_attempts")
    ]
    return {
        "sweeps_that_reserved": len(sweeps),
        "reserved": sum(item["report"]["scheduled_attempts"] for item in sweeps),
        "ms_per_reservation": distribution(
            [
                round(item["s"] * 1000 / item["report"]["scheduled_attempts"], 2)
                for item in sweeps
            ]
        ),
    }


def summarize(report, load, cluster, timeline, world):
    dispatch, claim, claims = latencies(world)
    per_attempt = Processes.calls_by_attempt(*(r["counter"] for r in load.runtimes))
    report["timeline"] = timeline
    report["admission_latency_s"] = {
        name: distribution(
            [
                a["s"]
                for a in load.admissions
                if a["phase"] == name and a["status"] == 201
            ]
        )
        for name in timeline
    }
    report["admission_request_s"] = {
        name: distribution(
            [
                a["service_s"]
                for a in load.admissions
                if a["phase"] == name and a["status"] == 201
            ]
        )
        for name in timeline
    }
    report["admission_refusals"] = sorted(
        {
            (a["phase"], a["status"], a["code"])
            for a in load.admissions
            if a["status"] != 201
        }
    )
    report["admission_tries"] = distribution([a["tries"] for a in load.admissions])
    report["dispatch_latency_ms"] = distribution(dispatch)
    report["claim_latency_ms"] = distribution(claim)
    report["activity_by_phase"] = activity_by_phase(world, timeline)
    report["criteria"] = load.criteria(timeline, per_attempt, claims)
    observed = [s for s in load.samples if "error" not in s]
    report["cursor_lock_waits"] = {
        "sampled_waiters": distribution([s["cursor_lock_waiters"] for s in observed]),
        "sampled_max_wait_s": distribution(
            [s["cursor_lock_wait_max_s"] for s in observed]
        ),
        "logged_over_100ms_ms": cluster.cursor_lock_waits_from_log(),
        # Every acquisition, including those a lock timeout ended (the logged
        # distribution above holds only waits that were eventually granted).
        "every_acquisition": cursor_waits(load.cursor_log),
    }
    report["lock_waits_by_relation"] = relation_waits(observed)
    report["gate_waits"] = {
        "by_caller": gate_waits(Path(os.environ[GATE_LOG]), timeline),
        "sampled_waiters": sampled_gate_waiters(observed),
    }
    report["revocation_probes"] = report["criteria"]["revocation_wait_bounded"]
    report["sweep_per_reservation"] = sweep_per_reservation(load.cycle_log)
    report["lookup_slot_waits"] = lookup_waits(load.lookup_log)
    report["service_calls"] = service_calls(load.call_log, timeline)
    report["deadlocks_logged"] = cluster.deadlocks_from_log()
    cycles = timing_log(load.cycle_log)
    report["worker_cycles_s"] = {
        kind: distribution([item["s"] for item in cycles if item["cycle"] == kind])
        for kind in ("progress", "sweep")
    }
    if len(observed) > 1:
        first, last = observed[0], observed[-1]
        report["wal_bytes_per_s"] = round(
            (last["wal_bytes"] - first["wal_bytes"])
            / ((last["at_ms"] - first["at_ms"]) / 1000)
        )
    report["database_cpu_percent"] = distribution(
        [
            s["database"]["cpu_percent"]
            for s in observed
            if s["database"]["cpu_percent"] is not None
        ]
    )
    report["samples"] = [
        {k: v for k, v in s.items() if k not in ("ages_ms", "runnable_ages_ms")}
        for s in load.samples
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="report directory")
    parser.add_argument(
        "--work", type=Path, required=True, help="private local world root"
    )
    parser.add_argument("--rate", type=float, default=5.0)
    parser.add_argument("--minutes", type=float, default=60.0)
    parser.add_argument("--burst-minutes", type=float, default=5.0)
    parser.add_argument("--recovery-minutes", type=float, default=10.0)
    parser.add_argument("--environments", type=int, default=8)
    parser.add_argument("--runtimes-per-environment", type=int, default=3)
    parser.add_argument("--waiting", type=int, default=10000)
    parser.add_argument("--sample-seconds", type=float, default=10.0)
    parser.add_argument(
        "--api-workers", type=int, default=1, help="API processes on one socket"
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="test-only: write each API process's cProfile into the report directory",
    )
    args = parser.parse_args()
    output = worlds.private_directory(args.output.absolute())
    report_path = output / "load-report.json"
    if report_path.exists():
        raise SystemExit("refusing to overwrite earlier evidence")
    report = {
        "format": "lotuspond.execution-load-report.v1",
        "parameters": {
            k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()
        },
        "hardware": hardware(),
        "outcome": "failed",
    }
    # Test-only timing logs the harness's API and workers append to.
    work = worlds.private_directory(args.work.absolute())
    os.environ[CURSOR_LOG] = str(work / "cursor-waits.jsonl")
    os.environ[CYCLE_LOG] = str(work / "cycles.jsonl")
    os.environ[LOOKUP_LOG] = str(work / "lookup-waits.jsonl")
    os.environ[CALL_LOG] = str(work / "service-calls.jsonl")
    os.environ[GATE_LOG] = str(work / "gate-waits.jsonl")
    if args.profile:
        os.environ[PROFILE_DIRECTORY] = str(output)

    def terminate(signum, frame):
        for other in (signal.SIGTERM, signal.SIGINT):  # a second one never cuts cleanup
            signal.signal(other, signal.SIG_IGN)
        raise SystemExit(128 + signum)  # unwinds: processes end, the cluster goes

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, terminate)
    cluster = Cluster()
    load = None
    try:
        cluster.start()
        report["cluster"] = {"image": IMAGE, "port": cluster.port}
        with worlds.owned(cluster.url, args.work.absolute() / "world") as world:
            processes = Processes(world, api_workers=args.api_workers)
            try:
                processes.start_api()
                workers = {
                    "orchestrator-a": "orchestrator",
                    "orchestrator-b": "orchestrator",
                }
                workers["sweeper-a"] = "sweeper"
                for name, role in workers.items():
                    processes.start_worker(role, name, wait=False)
                processes.wait_ready(*workers)
                load = Load(world, processes, cluster, args)
                load.setup()
                load.start_keeping()
                stop = threading.Event()
                sampler = threading.Thread(
                    target=load.sampler, args=(stop,), daemon=True
                )
                sampler.start()
                prober = threading.Thread(
                    target=load.revocations, args=(stop,), daemon=True
                )
                prober.start()
                timeline = load.drive()
                time.sleep(120)  # drain: admitted work finishes or waits
                stop.set()
                sampler.join(timeout=60)
                prober.join(timeout=60)
                summarize(report, load, cluster, timeline, world)
                if args.waiting:
                    report["waiting"] = load.waiting()
                version, settings, tables = table_health(world)
                report["postgresql"] = {"version": version, "settings": settings}
                report["tables"] = tables
                report["multixacts"] = multixact_growth(
                    [s for s in load.samples if "error" not in s], settings
                )
            finally:
                if load is not None:
                    load.stop_keeping()
                    report["runtime_restarts"] = load.restart_summary()
                report["process_cleanup"] = processes.close()
                for customer, _, _ in load.tenants if load else ():
                    customer.close()
        report["schema_dropped"] = True
    except BaseException as error:  # noqa: BLE001 - the report records the failure
        report["error"] = f"{type(error).__name__}: {str(error)[:300]}"
    finally:
        report["cluster_removed"] = cluster.remove()
        criteria = report.get("criteria", {})
        report["outcome"] = (
            "passed"
            if criteria
            and all(item["passed"] for item in criteria.values())
            and report["cluster_removed"]
            and report.get("process_cleanup", {}).get("confirmed")
            and "error" not in report
            else "failed"
        )
        report_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
        os.chmod(report_path, 0o600)
    return 0 if report["outcome"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
