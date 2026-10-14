"""Ephemeral synthetic localhost CA/server; never production trust material."""

import datetime
import socket
import ssl
import threading
from contextlib import contextmanager

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def certificates(directory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-runtime-test")])
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
    ca, private = directory / "synthetic-ca.pem", directory / "synthetic-key.pem"
    ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private.chmod(0o600)
    return ca, private


@contextmanager
def tls_server(directory, reply, *, trickle=False, body_trickle=False):
    ca, private = certificates(directory)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(ca, private)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    listener.settimeout(0.1)
    stop, requests = threading.Event(), []

    def serve():
        while not stop.is_set():
            try:
                raw, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                raw.settimeout(1)
                with context.wrap_socket(raw, server_side=True) as stream:
                    stream.settimeout(1)
                    request = b""
                    while b"\r\n\r\n" not in request:
                        request += stream.recv(8192)
                    requests.append(request)
                    response = reply(request) if callable(reply) else reply
                    if body_trickle:
                        header, _, response = response.partition(b"\r\n\r\n")
                        stream.sendall(header + b"\r\n\r\n")
                    if trickle or body_trickle:
                        for byte in response:
                            stream.sendall(bytes([byte]))
                            if stop.wait(0.02):
                                break
                    else:
                        stream.sendall(response)
            except (OSError, ssl.SSLError):
                raw.close()

    worker = threading.Thread(target=serve)
    worker.start()
    try:
        yield f"https://localhost:{listener.getsockname()[1]}", ca, requests
    finally:
        stop.set()
        listener.close()
        worker.join(3)
        assert not worker.is_alive()
