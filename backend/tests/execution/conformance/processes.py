"""Task 13: the parent side of a world's processes, and the runtimes' TCP proxy.

Every child starts in its own process group, so a whole group can be stopped,
continued or killed. `Processes.close()` ends what it started and returns a cleanup
receipt; a group that cannot be shown gone is reported unconfirmed, never assumed.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from hashlib import sha256
from pathlib import Path

import world as worlds

HARNESS = Path(__file__).with_name("harness.py")


def _absent(pid) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def wait_for(probe, seconds=20.0, interval=0.05):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("harness condition deadline")


class Proxy:
    """Byte forwarding from runtimes to the API that a test can stall or sever."""

    def __init__(self, target_port):
        self.target = target_port
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(128)
        self.port = self.listener.getsockname()[1]
        self.flowing = threading.Event()
        self.flowing.set()
        self.severed = False
        self.connections = set()
        self.lock = threading.Lock()
        self.closed = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self.closed:
            try:
                client, _ = self.listener.accept()
            except OSError:
                return
            if self.severed:
                client.close()
                continue
            try:
                upstream = socket.create_connection(("127.0.0.1", self.target), timeout=5)
            except OSError:
                client.close()
                continue
            with self.lock:
                self.connections.update({client, upstream})
            for source, sink in ((client, upstream), (upstream, client)):
                threading.Thread(target=self._pump, args=(source, sink), daemon=True).start()

    def _pump(self, source, sink):
        try:
            while True:
                data = source.recv(65536)
                if not data:
                    break
                self.flowing.wait()
                if self.severed:
                    break
                sink.sendall(data)
        except OSError:
            pass
        finally:
            for item in (source, sink):
                self._drop(item)

    def _drop(self, item):
        with self.lock:
            self.connections.discard(item)
        try:
            item.close()
        except OSError:
            pass

    def stall(self):
        """Connections stay open, but no byte moves either way."""
        self.flowing.clear()

    def sever(self):
        """Every connection is cut and new ones are refused."""
        self.severed = True
        self.flowing.set()
        with self.lock:
            items = list(self.connections)
        for item in items:
            try:
                item.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._drop(item)

    def restore(self):
        self.severed = False
        self.flowing.set()

    def close(self):
        self.closed = True
        self.sever()
        self.listener.close()


class Processes:
    """The API, workers and runtimes of one world, each in its own process group."""

    def __init__(self, world: worlds.World, *, python=None, cwd=None, api_workers=1):
        self.world = world
        self.api_workers = api_workers  # API processes on one socket; restarts keep it
        self.python = python or sys.executable
        self.cwd = cwd or Path(__file__).resolve().parents[3]
        self.children = {}
        self.proxy = None
        self.api_port = None
        self.ca, _ = None, None

    def _spawn(self, name, args, *, env=None):
        previous = self.children.get(name)
        if previous is not None and (previous.poll() is None or not _absent(previous.pid)):
            raise AssertionError(f"{name} is still running")  # never orphan a live group
        logs = worlds.private_directory(self.world.root / "logs")
        streams = [
            os.fdopen(
                os.open(logs / f"{name}.{kind}", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600),
                "ab",
            )
            for kind in ("stdout", "stderr")
        ]
        try:
            process = subprocess.Popen(
                args,
                cwd=self.cwd,
                env={**_clean_environment(), **(env or {})},
                stdout=streams[0],
                stderr=streams[1],
                start_new_session=True,
            )
        finally:
            for stream in streams:  # the child holds its own copies
                stream.close()
        self.children[name] = process
        return process

    # ---------------------------------------------------------------- API and workers

    def start_api(self):
        """Start the API; a restart comes back on the port clients already know."""
        path = self.world.path("api.json")
        if path.exists():
            path.unlink()
        args = [self.python, "-I", str(HARNESS), "api", str(self.world.root)]
        if self.api_port is not None:
            args += ["--port", str(self.api_port)]
        if self.api_workers != 1:
            args += ["--workers", str(self.api_workers)]
        process = self._spawn("api", args)
        state = wait_for(
            lambda: process.poll() is None and path.exists() and self.world.state("api"), 60
        )
        if process.poll() is not None:
            raise AssertionError("API process ended during startup")
        self.api_port = state["port"]
        self.ca = self.world.root / "synthetic-ca.pem"
        if self.proxy is None:
            self.proxy = Proxy(self.api_port)
        else:
            self.proxy.target = self.api_port
        return process

    def start_worker(
        self, role, name=None, *, interpreters=None, timeout_ms=15000, user=None, wait=True
    ):
        """Start a worker; by default, return once its first cycle has completed.

        `wait=False` returns at once: for a worker expected to refuse to start, or
        for several started together and then awaited with `wait_ready`.
        """
        name = name or f"{role}-{len(self.children)}"
        args = [self.python, "-I", str(HARNESS), "worker", str(self.world.root), "--role", role]
        args += ["--worker-id", name, "--timeout-ms", str(timeout_ms)]
        if user is not None:
            args += ["--database-user", user]
        for interpreter in interpreters or ():
            args += ["--interpreter", interpreter]
        (worlds.private_directory(self.world.root / "ready") / name).unlink(missing_ok=True)
        process = self._spawn(name, args)
        if wait:
            self.wait_ready(name)
        return process

    def wait_ready(self, *names, seconds=60):
        """Each named worker has completed its first cycle and is still running."""
        for name in names:
            process, marker = self.children[name], self.world.root / "ready" / name
            wait_for(lambda: marker.exists() or process.poll() is not None, seconds)
            if process.poll() is not None:
                raise AssertionError(f"worker {name} ended during startup: {process.returncode}")

    # ---------------------------------------------------------------- runtimes

    def start_runtime(
        self, name, enrollment_token, *, read=None, model=None, dependencies, start=True
    ):
        """Enroll and start one installed runtime with synthetic providers.

        `dependencies` gives the catalog pins: `connector` (ArtifactRef), `installed`
        (ConnectorPin) and `model` (ArtifactRef). Its calls are counted in its own
        `provider-calls` file, outside every database.
        """
        home = worlds.private_directory(self.world.root / "runtimes" / name)
        state = worlds.private_directory(home / "state")
        journal = worlds.private_directory(home / "journal")
        counter = worlds.private_directory(home / "read") / "provider-calls"
        model_counter = worlds.private_directory(home / "model") / "provider-calls"
        config = home / "config.json"
        worlds.write_private(
            config,
            {
                "origin": f"https://localhost:{self.proxy.port}",
                "ca_file": str(self.ca),
                "state_directory": str(state),
                "installation_id": worlds.INSTALLATION,
                "observation": {"platform": "linux", "connectors": []},
                "mode": "test",
            },
        )
        trust = home / "trust.json"
        worlds.write_private(trust, self.world.state("activity-trust"))
        read_script = {**(read or {"read": {"value": None}}), "counter": str(counter)}
        model_script = {**(model or {"rounds": [{"output": None}]}), "counter": str(model_counter)}
        worlds.write_private(
            home / "activity.json",
            {
                "trust_file": str(trust),
                "journal_directory": str(journal),
                "providers": [
                    {
                        "provider_id": "fixture-read",
                        "dependency": dependencies["connector"],
                        "operations": ["read"],
                        "connector": dependencies["installed"],
                        "script": read_script,
                    },
                    {
                        "provider_id": "fixture-model",
                        "dependency": dependencies["model"],
                        "script": model_script,
                    },
                ],
            },
        )
        token = home / "enrollment"
        for _ in range(20):
            worlds.write_private(token, enrollment_token)
            enrolled = subprocess.run(
                [self.python, "-I", "-m", "lotuspond_runtime", "enroll", "--config", str(config)]
                + ["--token-file", str(token)],
                cwd=home,
                env=_clean_environment(),
                capture_output=True,
                timeout=30,
            )
            token.unlink(missing_ok=True)
            # The API's single fleet lookup refuses while another is in flight (a
            # recorded Task 12 limitation); an operator runs the refused command again.
            if enrolled.returncode == 0 or b"unavailable" not in enrolled.stderr:
                break
            time.sleep(0.5)
        if enrolled.returncode != 0:
            raise AssertionError("runtime enrollment refused: " + enrolled.stderr.decode()[:200])
        instance = json.loads((state / "state.json").read_text())["instance_id"]
        if not start:
            return {
                "instance_id": instance,
                "counter": counter,
                "model_counter": model_counter,
                "home": home,
                "config": config,
            }
        return self.start_runtime_process(name)

    def start_runtime_process(self, name):
        """Start (or restart) the activity worker of an already enrolled runtime."""
        home = self.world.root / "runtimes" / name
        state = json.loads((home / "state" / "state.json").read_text())
        process = self._spawn(
            name,
            [self.python, "-I", "-m", "lotuspond_runtime", "activity-worker"]
            + ["--config", str(home / "config.json")]
            + ["--activity-config", str(home / "activity.json")],
        )
        return {
            "process": process,
            "instance_id": state["instance_id"],
            "counter": home / "read" / "provider-calls",
            "model_counter": home / "model" / "provider-calls",
            "home": home,
        }

    @staticmethod
    def calls(counter: Path) -> int:
        """Provider calls the synthetic providers observed, never assumed."""
        return len(counter.read_text().splitlines()) if counter.exists() else 0

    @staticmethod
    def calls_by_attempt(*counters: Path) -> dict:
        """Observed provider calls per attempt ID, over every given counter."""
        totals = {}
        for counter in counters:
            if counter.exists():
                for attempt in counter.read_text().splitlines():
                    totals[attempt] = totals.get(attempt, 0) + 1
        return totals

    # ---------------------------------------------------------------- signals

    def signal(self, name, signum):
        os.killpg(self.children[name].pid, signum)

    def kill(self, name):
        process = self.children[name]
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)
        wait_for(lambda: _absent(process.pid), 10)

    def stop(self, name, timeout=20):
        process = self.children[name]
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
        wait_for(lambda: _absent(process.pid), 10)
        return process.returncode

    def close(self) -> dict:
        """End every owned group; confirm each is gone."""
        groups = {}
        for name, process in list(self.children.items()):
            try:
                if process.poll() is None or not _absent(process.pid):
                    try:
                        os.killpg(process.pid, signal.SIGCONT)
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            pass  # reported below as not gone, never assumed
                deadline = time.monotonic() + 10
                while not _absent(process.pid) and time.monotonic() < deadline:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        break
                    time.sleep(0.05)
            except OSError:
                pass
            groups[name] = {
                "pid": process.pid,
                "exit": process.poll(),
                "gone": _absent(process.pid),
            }
        if self.proxy is not None:
            self.proxy.close()
        return {"groups": groups, "confirmed": all(item["gone"] for item in groups.values())}


def _clean_environment() -> dict:
    keep = {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR"}
    # Test-only measurement files (tier 3), never configuration.
    keep |= {"LOTUSPOND_CONFORMANCE_CURSOR_LOG", "LOTUSPOND_CONFORMANCE_CYCLE_LOG"}
    keep.add("LOTUSPOND_CONFORMANCE_PRIVILEGE_LOG")  # the worker grants, measured
    return {key: value for key, value in os.environ.items() if key in keep}


def catalog_ref(document) -> dict:
    from lotuspond_sdk.executable.catalog import catalog_document_digest

    return {
        "kind": document["kind"],
        "name": document["name"],
        "version": document["version"],
        "sha256": catalog_document_digest(document),
    }


def digest(content: bytes) -> str:
    return sha256(content).hexdigest()


class Hand:
    """One enrolled runtime instance driven step by step from the test, over the
    real runtime protocol (its own credential, session, TLS and the proxy).

    For cases that must act between a poll and a claim, which a running worker
    process does at once.
    """

    def __init__(self, processes, enrolled):
        from lotuspond_runtime.activity_client import ActivityClient
        from lotuspond_runtime.client import RuntimeClient
        from lotuspond_runtime.control_transport import ControlTransport
        from lotuspond_runtime.state import CredentialStore

        config = json.loads(enrolled["config"].read_text())
        self.store = CredentialStore(Path(config["state_directory"]))
        state = self.store.load()
        self.token = state.active_token
        origin, ca = config["origin"], Path(config["ca_file"])
        self.control = RuntimeClient(ControlTransport(origin, ca, mode="test"))
        acquired = self.control.acquire(self.token)
        self.session, self.identity = acquired.secret, acquired.identity
        self.transport = ControlTransport(origin, ca, mode="test")
        self.activity = ActivityClient(self.transport, self.token, self.session)

    def close(self):
        for item in (self.control, self.transport):
            try:
                item.close()
            except Exception:  # noqa: BLE001 - best-effort test cleanup
                pass
        self.store.close()
