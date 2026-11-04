"""Per-request CPU and latency of the runtime ControlTransport against a TLS uvicorn.

Synthetic only: an ephemeral localhost CA, and an ASGI app that answers every
request with `{}`. The client runs in this process; the server in a child that
reports its own CPU on a side path. Client CPU includes reaped resolver children.

    backend/.venv/bin/python scripts/checks/control-transport-cost.py [--requests N]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import resource
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SERVER = """
import json, resource, signal, socket, sys, uvicorn

async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    body = b"{}"
    if scope["path"] == "/cpu":  # the benchmark's own mark, outside the measured loop
        usage = resource.getrusage(resource.RUSAGE_SELF)
        body = json.dumps(usage.ru_utime + usage.ru_stime).encode()
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})

listener = socket.socket()
listener.bind(("127.0.0.1", 0))
listener.listen(128)
print(listener.getsockname()[1], flush=True)
server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False,
                                       ssl_certfile=sys.argv[1], ssl_keyfile=sys.argv[2]))
server.run(sockets=[listener])
"""


def certificates(directory: Path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-transport-cost")]
    )
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
        .sign(key, hashes.SHA256())
    )
    ca, private = directory / "ca.pem", directory / "key.pem"
    ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return ca, private


def server_cpu(port, ca):
    import http.client
    import ssl

    connection = http.client.HTTPSConnection(
        "localhost", port, context=ssl.create_default_context(cafile=str(ca)), timeout=5
    )
    try:
        connection.request("GET", "/cpu")
        return json.loads(connection.getresponse().read())
    finally:
        connection.close()


def cpu():
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return own.ru_utime + own.ru_stime, children.ru_utime + children.ru_stime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=300)
    parser.add_argument(
        "--pause", type=float, default=0.0, help="seconds between requests"
    )
    arguments = parser.parse_args()

    from lotuspond_runtime.control_transport import ControlTransport
    from pydantic import SecretStr

    token = SecretStr("lpr_" + "A" * 43)
    with tempfile.TemporaryDirectory() as directory:
        ca, key = certificates(Path(directory))
        server = subprocess.Popen(
            [sys.executable, "-c", SERVER, str(ca), str(key)],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            port = int(server.stdout.readline())
            for _ in range(100):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=1).close()
                    break
                except OSError:
                    time.sleep(0.05)
            transport = ControlTransport(f"https://localhost:{port}", ca, mode="test")
            transport.request("GET", "/self", None, token)  # warm imports and caches
            latencies = []
            server0 = server_cpu(port, ca)
            own0, children0 = cpu()
            for _ in range(arguments.requests):
                started = time.perf_counter()
                assert transport.request("GET", "/self", None, token) == b"{}"
                latencies.append(time.perf_counter() - started)
                if arguments.pause:
                    time.sleep(arguments.pause)
            own1, children1 = cpu()
            server1 = server_cpu(port, ca)
            transport.close()
        finally:
            server.send_signal(signal.SIGTERM)
            server.communicate(timeout=10)
    n = arguments.requests
    latencies.sort()
    print(
        json.dumps(
            {
                "requests": n,
                "pause_s": arguments.pause,
                "python": sys.version.split()[0],
                "client_cpu_ms": round((own1 - own0) * 1000 / n, 3),
                "resolver_children_cpu_ms": round(
                    (children1 - children0) * 1000 / n, 3
                ),
                "server_cpu_ms": round((server1 - server0) * 1000 / n, 3),
                "latency_ms_p50": round(statistics.median(latencies) * 1000, 3),
                "latency_ms_p95": round(latencies[int(n * 0.95) - 1] * 1000, 3),
            }
        )
    )


if __name__ == "__main__":
    os.environ.pop("HTTPS_PROXY", None)
    main()
