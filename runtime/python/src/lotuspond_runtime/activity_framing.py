"""Length-prefixed, bounded JSON frames between the activity worker and its child."""

from __future__ import annotations

import json
import struct
import time

from lotuspond_sdk.executable.input import JsonLimits, decode_executable_bytes, project_json

from .errors import FleetError

# A model round carries its resolved context and transcript; 1 MiB bounds both.
MAX_FRAME = 1048576
LIMITS = JsonLimits(MAX_FRAME, 32, 10000)
_SLICE_S = 0.05


class Cancelled(Exception):
    """The caller's cancellation was observed while a frame was in transit."""


def _remaining(deadline_ns):
    left = (deadline_ns - time.monotonic_ns()) / 1e9
    if left <= 0:
        raise FleetError("transport_failed")
    return left


def encode(value) -> bytes:
    data = json.dumps(value, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    if len(data) > MAX_FRAME:
        raise FleetError("input_limit")
    return data


def send(channel, value, deadline_ns):
    data = encode(value)
    channel.settimeout(_remaining(deadline_ns))
    channel.sendall(struct.pack("!I", len(data)) + data)


def receive(channel, deadline_ns, cancel=None):
    """One frame; with `cancel`, waits in short slices and stops once it is set."""

    def read(count):
        data = bytearray()
        while len(data) < count:
            left = _remaining(deadline_ns)
            if cancel is not None:
                if cancel.is_set():
                    raise Cancelled
                left = min(left, _SLICE_S)
            channel.settimeout(left)
            try:
                chunk = channel.recv(min(65536, count - len(data)))
            except TimeoutError:
                if cancel is None:
                    raise FleetError("transport_failed") from None
                continue
            if not chunk:
                raise FleetError("transport_failed")
            data.extend(chunk)
        return bytes(data)

    (length,) = struct.unpack("!I", read(4))
    if length > MAX_FRAME:
        raise FleetError("input_limit")
    try:
        return project_json(decode_executable_bytes(read(length)), limits=LIMITS)
    except (FleetError, Cancelled):
        raise
    except Exception:
        raise FleetError("transport_failed") from None
