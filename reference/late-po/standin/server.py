"""Synthetic stand-in ERP, carrier and inbox for the late purchase order reference workflow.

One local HTTPS process serves three systems, each under its own route prefix (`/erp`,
`/carrier`, `/inbox`) and its own bearer token, so the package needs three connections
with three secrets. It serves the records in `../data/late-po.json`, plus two writes that
take an idempotency key and can be read back: the ERP's promised-date change, which also
moves the order's promised day, and the customer update sent from the inbox. Nothing here
is a customer system or a real integration.

`answer(operation, target, input)` is the whole behaviour; the HTTP layer only maps routes
onto it, so a scripted run and an HTTP run return the same values.

Run: `python reference/late-po/standin/server.py --port 8443 --cert C --key K
--token-dir D`, where D holds `erp`, `carrier` and `inbox` token files.
"""

from __future__ import annotations

import argparse
import copy
import hmac
import json
import re
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "late-po.json"
MAX_BODY = 16384
SYSTEMS = ("erp", "carrier", "inbox")


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


class Ledger:
    """The records plus the writes made since start; writes are idempotent per key."""

    def __init__(self, data_path=DATA):
        self.data = json.loads(Path(data_path).read_text())
        self.date_changes: dict[str, dict] = {}
        self.customer_updates: dict[str, dict] = {}
        self.keys: dict[str, tuple[str, str]] = {}
        self.fail_after_commit = 0
        self.lock = threading.Lock()
        self.requests: list[tuple[str, str, bool]] = []  # (method, path, authorized)

    def _order(self, po_number):
        try:
            return copy.deepcopy(self.data["purchase_orders"][po_number])
        except KeyError:
            raise NotFound from None

    def _sales_order(self, sales_order_id):
        for order in self.data["purchase_orders"].values():
            if order["sales_order_id"] == sales_order_id:
                return order
        raise NotFound

    def answer(self, operation, target, input_=None, idempotency_key=None):
        target = target or {}
        if operation == "get_purchase_order":
            return self._order(target["po_number"])
        if operation == "get_stock_cover":
            cover = self.data["stock_cover"].get(target["sku"])
            if cover is None:
                raise NotFound
            return {"sku": target["sku"], "days_of_cover": cover}
        if operation == "get_shipment":
            self._order(target["po_number"])
            return copy.deepcopy(self.data["shipments"][target["po_number"]])
        if operation == "list_tracking_events":
            self._order(target["po_number"])
            return {
                "events": copy.deepcopy(self.data["tracking_events"].get(target["po_number"], []))
            }
        if operation == "list_supplier_messages":
            self._order(target["po_number"])
            messages = self.data["supplier_messages"].get(target["po_number"], [])
            return {"messages": copy.deepcopy(messages)}
        if operation == "get_promised_date_change":
            self._order(target["po_number"])
            return self._readback(self.date_changes.get(target["po_number"]))
        if operation == "get_customer_update":
            self._sales_order(target["sales_order_id"])
            return self._readback(self.customer_updates.get(target["sales_order_id"]))
        if operation == "set_promised_date":
            return self._write("set_promised_date", target, input_, idempotency_key)
        if operation == "send_customer_update":
            return self._write("send_customer_update", target, input_, idempotency_key)
        raise NotFound

    @staticmethod
    def _readback(found):
        if found is None:
            return {"exists": False, "promised_day": 0, "reason": ""}
        return {"exists": True, "promised_day": found["promised_day"], "reason": found["reason"]}

    def _write(self, operation, target, input_, key):
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise Invalid
        if not isinstance(input_, dict) or type(input_.get("promised_day")) is not int:
            raise Invalid
        if operation == "set_promised_date":
            subject = self._order(target["po_number"])["po_number"]
        else:
            subject = self._sales_order(target["sales_order_id"])["sales_order_id"]
        fingerprint = json.dumps([operation, target, input_], sort_keys=True)
        with self.lock:
            if key in self.keys:
                stored, reference = self.keys[key]
                if stored != fingerprint:
                    raise Invalid  # a reused key with different content is refused
                return {"reference": reference}
            record = {
                "promised_day": input_["promised_day"],
                "reason": str(input_.get("reason", ""))[:400],
            }
            if operation == "set_promised_date":
                reference = f"DATE-{subject}"
                self.date_changes[subject] = record
                self.data["purchase_orders"][subject]["promised_day"] = input_["promised_day"]
            else:
                reference = f"MAIL-{subject}"
                # The inbox sends a fixed template; only the day and the reason vary.
                record["message"] = (
                    f"Your order {subject} will now arrive on day {input_['promised_day']}. "
                    f"{record['reason']}"
                )
                self.customer_updates[subject] = record
            self.keys[key] = (fingerprint, reference)
            if self.fail_after_commit > 0:
                self.fail_after_commit -= 1
                raise TimeoutError  # committed, but the caller never hears so
        return {"reference": reference}


# ------------------------------------------------------------------ HTTP

ROUTES = [
    ("erp", "GET", r"/erp/purchase-orders/(?P<po_number>[\w-]+)", "get_purchase_order"),
    ("erp", "GET", r"/erp/stock/(?P<sku>[\w-]+)", "get_stock_cover"),
    (
        "erp",
        "GET",
        r"/erp/purchase-orders/(?P<po_number>[\w-]+)/promised-date",
        "get_promised_date_change",
    ),
    (
        "erp",
        "POST",
        r"/erp/purchase-orders/(?P<po_number>[\w-]+)/promised-date",
        "set_promised_date",
    ),
    ("carrier", "GET", r"/carrier/shipments/(?P<po_number>[\w-]+)", "get_shipment"),
    ("carrier", "GET", r"/carrier/shipments/(?P<po_number>[\w-]+)/events", "list_tracking_events"),
    ("inbox", "GET", r"/inbox/purchase-orders/(?P<po_number>[\w-]+)", "list_supplier_messages"),
    ("inbox", "GET", r"/inbox/sent/(?P<sales_order_id>[\w-]+)", "get_customer_update"),
    ("inbox", "POST", r"/inbox/sent/(?P<sales_order_id>[\w-]+)", "send_customer_update"),
]


def route(method, path):
    """(system, operation, target) for a request, or (None, None, None)."""
    for system, verb, pattern, operation in ROUTES:
        match = re.fullmatch(pattern, path)
        if verb == method and match:
            return system, operation, match.groupdict()
    return None, None, None


def handler(ledger: Ledger, tokens: dict[str, str]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "lotuspond-late-po-standin"
        sys_version = ""

        def log_message(self, *args):  # no request logging: paths carry record IDs
            pass

        def _send(self, status, value):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle(self, method):
            system, operation, target = route(method, self.path.split("?", 1)[0])
            supplied = self.headers.get("Authorization", "").encode()
            # Each system accepts only its own token: one connection, one secret.
            authorized = system is not None and hmac.compare_digest(
                supplied, f"Bearer {tokens[system]}".encode()
            )
            ledger.requests.append((method, self.path, authorized))
            if system is None:
                return self._send(404, {"error": "not_found"})
            if not authorized:
                return self._send(401, {"error": "unauthorized"})
            input_ = None
            if method == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length <= MAX_BODY:
                    return self._send(400, {"error": "invalid_request"})
                try:
                    input_ = json.loads(self.rfile.read(length))
                except ValueError:
                    return self._send(400, {"error": "invalid_request"})
            try:
                value = ledger.answer(
                    operation, target, input_, self.headers.get("Idempotency-Key")
                )
            except NotFound:
                return self._send(404, {"error": "not_found"})
            except Invalid:
                return self._send(422, {"error": "invalid_request"})
            except TimeoutError:
                self.close_connection = True
                return None  # committed; the connection closes with no response
            return self._send(201 if method == "POST" else 200, value)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

    return Handler


def serve(port, cert, key, tokens, ledger=None, host="127.0.0.1"):
    if set(tokens) != set(SYSTEMS):
        raise ValueError("one token per system: erp, carrier, inbox")
    ledger = ledger or Ledger()
    server = ThreadingHTTPServer((host, port), handler(ledger, tokens))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    return server, ledger


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--token-dir", required=True)
    parser.add_argument("--fail-after-commit", type=int, default=0)
    args = parser.parse_args()
    tokens = {s: (Path(args.token_dir) / s).read_text().strip() for s in SYSTEMS}
    server, ledger = serve(args.port, args.cert, args.key, tokens)
    ledger.fail_after_commit = args.fail_after_commit
    server.serve_forever()


if __name__ == "__main__":
    main()
