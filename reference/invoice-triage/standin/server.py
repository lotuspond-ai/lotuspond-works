"""Synthetic stand-in AP system for the invoice-triage reference workflow.

It serves the records in `../data/ap.json` over HTTPS JSON, plus two writes (a payment
hold and a credit-note request) that take an idempotency key and can be read back. It
is not a customer system or an ERP integration, and it holds no real data.

`answer(operation, target, input)` is the whole behaviour; the HTTP layer only maps
routes onto it. The conformance harness uses `answer` to script synthetic providers,
so a scripted run and an HTTP run return the same values.

Run: `python reference/invoice-triage/standin/server.py --port 8443 --cert C --key K
--token-file T`. Every request needs `Authorization: Bearer <token>`.
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

DATA = Path(__file__).resolve().parents[1] / "data" / "ap.json"
MAX_BODY = 16384


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


class Ledger:
    """The records plus the writes made since start; writes are idempotent per key."""

    def __init__(self, data_path=DATA):
        self.data = json.loads(Path(data_path).read_text())
        self.holds: dict[str, dict] = {}
        self.credit_requests: dict[str, dict] = {}
        self.keys: dict[str, tuple[str, str, str]] = {}
        self.fail_after_commit = 0
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ reads

    def _record(self, family, key):
        try:
            return copy.deepcopy(self.data[family][key])
        except KeyError:
            raise NotFound from None

    def answer(self, operation, target, input_=None, idempotency_key=None):
        target = target or {}
        if operation == "get_invoice":
            return self._record("invoices", target["invoice_id"])
        if operation == "get_purchase_order":
            return self._record("purchase_orders", target["po_number"])
        if operation == "get_goods_receipt":
            receipt = self.data["goods_receipts"].get(target["po_number"])
            return copy.deepcopy(receipt) or {
                "po_number": target["po_number"],
                "received_quantity": 0,
                "receipts": [],
            }
        if operation == "get_supplier_contract":
            return self._record("contracts", target["supplier_id"])
        if operation == "list_supplier_invoices":
            invoices = [
                copy.deepcopy(invoice)
                for invoice in self.data["invoices"].values()
                if invoice["supplier_id"] == target["supplier_id"]
                and invoice["po_number"] == target["po_number"]
            ]
            return {"invoices": sorted(invoices, key=lambda item: item["invoice_id"])}
        if operation == "list_credit_notes":
            notes = self.data["credit_notes"].get(target["supplier_id"], [])
            return {"credit_notes": copy.deepcopy(notes)}
        if operation == "get_payment_hold":
            return self._readback(self.holds, target["invoice_id"])
        if operation == "get_credit_note_request":
            return self._readback(self.credit_requests, target["invoice_id"])
        if operation == "set_payment_hold":
            return self._write(self.holds, "HOLD", target, input_, idempotency_key)
        if operation == "request_credit_note":
            return self._write(self.credit_requests, "CNR", target, input_, idempotency_key)
        raise NotFound

    def _readback(self, store, invoice_id):
        self._record("invoices", invoice_id)
        found = store.get(invoice_id)
        if found is None:
            return {"exists": False, "amount": "0.00", "reason": ""}
        return {"exists": True, "amount": found["amount"], "reason": found["reason"]}

    # ------------------------------------------------------------------ writes

    def _write(self, store, prefix, target, input_, key):
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise Invalid
        invoice = self._record("invoices", target["invoice_id"])
        if not isinstance(input_, dict) or not re.fullmatch(
            r"-?\d+\.\d{2}", str(input_.get("amount"))
        ):
            raise Invalid
        fingerprint = json.dumps([prefix, target, input_], sort_keys=True)
        with self.lock:
            if key in self.keys:
                stored_fingerprint, reference, _ = self.keys[key]
                if stored_fingerprint != fingerprint:
                    raise Invalid  # a reused key with different content is refused
                return {"reference": reference}
            reference = f"{prefix}-{invoice['invoice_id']}"
            store[invoice["invoice_id"]] = {
                "reference": reference,
                "amount": input_["amount"],
                "reason": str(input_.get("reason", ""))[:400],
            }
            self.keys[key] = (fingerprint, reference, invoice["invoice_id"])
            if self.fail_after_commit > 0:
                self.fail_after_commit -= 1
                raise TimeoutError  # committed, but the caller never hears so
        return {"reference": reference}


# ------------------------------------------------------------------ HTTP

ROUTES = [
    ("GET", r"/invoices/(?P<invoice_id>[\w-]+)", "get_invoice"),
    ("GET", r"/purchase-orders/(?P<po_number>[\w-]+)", "get_purchase_order"),
    ("GET", r"/goods-receipts/(?P<po_number>[\w-]+)", "get_goods_receipt"),
    ("GET", r"/suppliers/(?P<supplier_id>[\w-]+)/contract", "get_supplier_contract"),
    (
        "GET",
        r"/suppliers/(?P<supplier_id>[\w-]+)/purchase-orders/(?P<po_number>[\w-]+)/invoices",
        "list_supplier_invoices",
    ),
    ("GET", r"/suppliers/(?P<supplier_id>[\w-]+)/credit-notes", "list_credit_notes"),
    ("GET", r"/invoices/(?P<invoice_id>[\w-]+)/payment-hold", "get_payment_hold"),
    ("GET", r"/invoices/(?P<invoice_id>[\w-]+)/credit-note-request", "get_credit_note_request"),
    ("POST", r"/invoices/(?P<invoice_id>[\w-]+)/payment-hold", "set_payment_hold"),
    ("POST", r"/invoices/(?P<invoice_id>[\w-]+)/credit-note-request", "request_credit_note"),
]


def route(method, path):
    for verb, pattern, operation in ROUTES:
        match = re.fullmatch(pattern, path)
        if verb == method and match:
            return operation, match.groupdict()
    return None, None


def handler(ledger: Ledger, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "lotuspond-ap-standin"
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
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
                return self._send(401, {"error": "unauthorized"})
            operation, target = route(method, self.path.split("?", 1)[0])
            if operation is None:
                return self._send(404, {"error": "not_found"})
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


def serve(port, cert, key, token, ledger=None, host="127.0.0.1"):
    ledger = ledger or Ledger()
    server = ThreadingHTTPServer((host, port), handler(ledger, token))
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
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--fail-after-commit", type=int, default=0)
    args = parser.parse_args()
    token = Path(args.token_file).read_text().strip()
    server, ledger = serve(args.port, args.cert, args.key, token)
    ledger.fail_after_commit = args.fail_after_commit
    server.serve_forever()


if __name__ == "__main__":
    main()
