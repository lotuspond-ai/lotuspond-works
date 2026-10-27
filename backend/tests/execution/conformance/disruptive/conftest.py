"""Task 13 tier 2: disruptive cases on the suite's own disposable PostgreSQL cluster.

Opt-in with `LOTUSPOND_DISRUPTIVE_EXECUTION_TESTS=1`. Each case starts a fresh
`postgres:18.6` container published only on a private 127.0.0.1 port (never 5432,
55439 or a development database), stops it with `pg_ctl -m immediate`, backs it up
and restores it, then removes it and confirms the removal. Cluster-wide roles exist
only inside that container (owner decision, 2026-10-26).
"""

from __future__ import annotations

import os
import random
import shutil
import socket
import subprocess
import time
from uuid import uuid4

import pytest

from ..conftest import owned_world

DISRUPTIVE = os.environ.get("LOTUSPOND_DISRUPTIVE_EXECUTION_TESTS") == "1"
IMAGE = "postgres:18.6"
DATA = "/var/lib/postgresql/18/docker"
REFUSED_PORTS = {5432, 55439}
LABEL = "com.lotuspond.conformance=disruptive"


def _docker(*args, input=None, timeout=120, check=True):
    return subprocess.run(
        ["docker", *args], input=input, capture_output=True, timeout=timeout, check=check
    )


def _free_port():
    for _ in range(100):
        port = random.randint(20000, 60000)
        if port in REFUSED_PORTS:
            continue
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise AssertionError("no private port")


class Cluster:
    """One disposable cluster: immediate restart, logical backup and restore."""

    def __init__(self):
        self.name = "lotuspond-conformance-" + uuid4().hex[:12]
        self.port = _free_port()
        self.url = f"postgresql+psycopg://lotuspond@127.0.0.1:{self.port}/lotuspond_platform_test"

    def start(self):
        _docker(
            "run",
            "--detach",
            "--name",
            self.name,
            "--label",
            LABEL,
            "--publish",
            f"127.0.0.1:{self.port}:5432",
            "--env",
            "POSTGRES_USER=lotuspond",
            "--env",
            "POSTGRES_DB=lotuspond_platform_test",
            "--env",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            "--env",
            f"PGDATA={DATA}",
            IMAGE,
        )
        self.ready()

    def ready(self, seconds=90):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            probe = _docker(
                "exec",
                self.name,
                "pg_isready",
                "-h",
                "127.0.0.1",
                "-U",
                "lotuspond",
                "-d",
                "lotuspond_platform_test",
                check=False,
            )
            if probe.returncode == 0:
                with socket.socket() as connection:
                    if connection.connect_ex(("127.0.0.1", self.port)) == 0:
                        return
            time.sleep(0.5)
        raise AssertionError("disposable cluster never became ready")

    def psql(self, sql, database="lotuspond_platform_test"):
        return (
            _docker(
                "exec",
                self.name,
                "psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "lotuspond",
                "-d",
                database,
                "-Atc",
                sql,
            )
            .stdout.decode()
            .strip()
        )

    def immediate_restart(self):
        """`pg_ctl -m immediate`: no shutdown checkpoint; the next start replays WAL."""
        _docker(
            "exec",
            "--user",
            "postgres",
            self.name,
            "pg_ctl",
            "-D",
            DATA,
            "-m",
            "immediate",
            "stop",
            check=False,
        )
        _docker("wait", self.name, timeout=60, check=False)
        _docker("start", self.name)
        self.ready()

    def backup(self):
        return _docker(
            "exec",
            self.name,
            "pg_dump",
            "-U",
            "lotuspond",
            "-Fc",
            "lotuspond_platform_test",
            timeout=300,
        ).stdout

    def restore(self, dump):
        """The database as it was at `backup()`; every connection to it is ended first."""
        self.psql(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = 'lotuspond_platform_test' AND pid <> pg_backend_pid()",
            database="postgres",
        )
        self.psql("DROP DATABASE lotuspond_platform_test WITH (FORCE)", database="postgres")
        self.psql("CREATE DATABASE lotuspond_platform_test OWNER lotuspond", database="postgres")
        _docker(
            "exec",
            "-i",
            self.name,
            "pg_restore",
            "-U",
            "lotuspond",
            "-d",
            "lotuspond_platform_test",
            "--exit-on-error",
            input=dump,
            timeout=300,
        )

    def remove(self) -> bool:
        """Remove the container and its anonymous volumes; confirm both are gone.

        Its volumes carry no label, so they are named before removal and checked
        after it, never inferred gone.
        """
        mounts = _docker(
            "inspect",
            "--format",
            "{{range .Mounts}}{{.Name}} {{end}}",
            self.name,
            check=False,
        )
        volumes = mounts.stdout.decode().split() if mounts.returncode == 0 else []
        _docker("rm", "--force", "--volumes", self.name, check=False)
        left = _docker("ps", "--all", "--quiet", "--filter", f"name=^{self.name}$", check=False)
        kept = [
            volume
            for volume in volumes
            if _docker("volume", "inspect", volume, check=False).returncode == 0
        ]
        return left.returncode == 0 and not left.stdout.strip() and not kept


@pytest.fixture
def cluster():
    # Tier 2 is opt-in (its own job), so a tier 1 run skips it even in required mode;
    # once opted in, a missing prerequisite fails.
    if not DISRUPTIVE:
        pytest.skip("tier 2 is opt-in: LOTUSPOND_DISRUPTIVE_EXECUTION_TESTS=1")
    if shutil.which("docker") is None:
        pytest.fail("tier 2 requires a Docker CLI")
    owned = Cluster()
    try:
        owned.start()
        yield owned
    finally:
        assert owned.remove(), "disposable cluster removal unconfirmed"


@pytest.fixture
def world(engine_name, tmp_path, cluster):
    """The world on the disposable cluster instead of the shared test database."""
    with owned_world(cluster.url, tmp_path / "world") as created:
        created.cluster = cluster
        yield created
