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
                        chunk = stream.recv(8192)
                        if not chunk:
                            break
                        request += chunk
                    if b"\r\n\r\n" not in request:
                        continue
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


class KeptServer:
    """A synthetic keep-alive TLS server: many requests per connection, numbered."""

    def __init__(self, directory, reply):
        self.directory, self.reply = directory, reply
        self.ca, self.private = certificates(directory)
        self.context = self._server_context(self.ca, self.private)
        self.requests = []  # (connection number, request bytes)
        self.connections = 0
        self.live = set()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.listener.settimeout(0.1)
        self.origin = f"https://localhost:{self.listener.getsockname()[1]}"
        self.threads = [threading.Thread(target=self._accept)]
        self.threads[0].start()

    @staticmethod
    def _server_context(ca, private):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(ca, private)
        return context

    def _accept(self):
        while not self.stop.is_set():
            try:
                raw, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with self.lock:
                self.connections += 1
                number = self.connections
            thread = threading.Thread(target=self._serve, args=(raw, number, self.context))
            self.threads.append(thread)
            thread.start()

    def _serve(self, raw, number, context):
        try:
            raw.settimeout(5)
            with context.wrap_socket(raw, server_side=True) as stream:
                with self.lock:
                    self.live.add(stream)
                data = b""
                while not self.stop.is_set():
                    while b"\r\n\r\n" not in data:
                        chunk = stream.recv(8192)
                        if not chunk:
                            return
                        data += chunk
                    head, _, data = data.partition(b"\r\n\r\n")
                    size = 0
                    for line in head.split(b"\r\n")[1:]:
                        key, _, value = line.partition(b":")
                        if key.lower() == b"content-length":
                            size = int(value)
                    while len(data) < size:
                        data += stream.recv(8192)
                    request, data = head + b"\r\n\r\n" + data[:size], data[size:]
                    self.requests.append((number, request))
                    stream.sendall(self.reply(request))
        except (OSError, ssl.SSLError, ValueError):
            pass
        finally:
            with self.lock:
                self.live = {item for item in self.live if item.fileno() != -1}
            raw.close()

    def drop(self):
        """A restart on the same port: every live connection ends."""
        with self.lock:
            items = list(self.live)
            self.live.clear()
        for item in items:
            try:
                item.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def rotate(self):
        """A restart under a new synthetic CA and certificate; returns the new CA file."""
        other = self.directory / f"rotated-{self.connections}"
        other.mkdir()
        ca, private = certificates(other)
        self.context = self._server_context(ca, private)
        self.drop()
        return ca

    def push(self, data):
        """Unsolicited bytes on every live connection, between responses."""
        with self.lock:
            items = list(self.live)
        for item in items:
            item.sendall(data)

    def close(self):
        self.stop.set()
        self.drop()
        self.listener.close()
        for thread in self.threads:
            thread.join(6)
        assert not any(thread.is_alive() for thread in self.threads)


@contextmanager
def kept_server(directory, reply):
    server = KeptServer(directory, reply)
    try:
        yield server
    finally:
        server.close()
