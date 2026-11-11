"""Writes the invoice-triage reference package, its prefix and its catalog documents.

Run from the repository root:
`uv run --frozen --project backend python reference/invoice-triage/generate.py`.

Every document is synthetic (see README.md). The full package declares the payment
hold and credit-note actions; E04 refuses those steps until E05, so the prefix
package (the same workflow with both action branches replaced by the clerk's form)
is what the conformance harness runs today. Both are compiled with the SDK here.
"""

import copy
import json
from hashlib import sha256
from pathlib import Path

import rfc8785
from lotuspond_sdk.executable import compile_package
from lotuspond_sdk.executable.catalog import catalog_document_digest

OUT = Path(__file__).resolve().parent
VERSION = "0.1.0"


# ------------------------------------------------------------------ mapping helpers


def lit(value, schema):
    return {"kind": "literal", "schema": schema, "value": value}


def ref(pointer="", source=None):
    return {"kind": "ref", "source": source or {"kind": "package_input"}, "pointer": pointer}


def step(sid, pointer=""):
    return ref(pointer, {"kind": "step", "id": sid})


def call(fn, *args, **extra):
    return {"kind": "call", "fn": fn, "args": list(args), **extra}


def obj(**fields):
    return {"kind": "object", "fields": [{"name": k, "value": v} for k, v in fields.items()]}


def data(schema):
    return {"kind": "data", "schema": schema}


def body(steps, result, schema):
    return {"steps": steps, "result": result, "result_schema": data(schema)}


def ref_of(doc):
    return {
        "kind": doc["kind"],
        "name": doc["name"],
        "version": doc["version"],
        "sha256": catalog_document_digest(doc),
    }


# ------------------------------------------------------------------ schemas


def string(n):
    return {"kind": "string", "max_length": n}


def fields(**spec):
    return {
        "kind": "object",
        "fields": [{"name": k, "schema": v, "required": True} for k, v in spec.items()],
    }


def array(items, n):
    return {"kind": "array", "items": items, "max_items": n}


NULL = {"kind": "null"}
BOOL = {"kind": "boolean"}
ID = string(32)
MONEY = {"kind": "decimal", "precision": 18, "scale": 2}
FACTOR = {"kind": "decimal", "precision": 8, "scale": 2}
QTY = {"kind": "integer", "min": 0, "max": 1_000_000}
LINE = fields(sku=ID, quantity=QTY, unit_price=MONEY)
INVOICE = fields(
    invoice_id=ID,
    supplier_id=ID,
    po_number=ID,
    currency=string(3),
    total=MONEY,
    total_quantity=QTY,
    status=string(16),
    lines=array(LINE, 50),
)
ORDER = fields(
    po_number=ID,
    supplier_id=ID,
    currency=string(3),
    total=MONEY,
    total_quantity=QTY,
    invoiced_quantity=QTY,
    lines=array(LINE, 50),
)
RECEIPT = fields(
    po_number=ID,
    received_quantity=QTY,
    receipts=array(fields(receipt_id=ID, sku=ID, quantity=QTY), 50),
)
CONTRACT = fields(
    supplier_id=ID,
    prices=array(fields(sku=ID, unit_price=MONEY, effective_from=string(10)), 50),
    price_change_terms=string(500),
)
INVOICES = fields(invoices=array(INVOICE, 50))
CREDIT_NOTES = fields(
    credit_notes=array(
        fields(credit_note_id=ID, invoice_id=ID, amount=MONEY, status=string(16)), 50
    )
)
VARIANCE = fields(
    price_delta={"kind": "decimal", "precision": 38, "scale": 2},
    quantity_needed={"kind": "integer"},
)
REASON = fields(text=string(400), tool=string(64), field=string(128))
PROPOSAL = fields(exception_class=ID, resolution=ID, amount=MONEY, reasons=array(REASON, 5))
INVESTIGATION = fields(invoice=INVOICE, order=ORDER, receipt=RECEIPT, variance=VARIANCE)
FORM = fields(decision=ID, comment=string(1000))
TRIAGE = fields(
    status=ID,
    exception_class=ID,
    resolution=ID,
    amount=MONEY,
    reasons=array(REASON, 5),
    clerk_decision=ID,
    comment=string(1000),
)
HOLD_READBACK = fields(exists=BOOL, amount=MONEY, reason=string(400))
CREDIT_READBACK = fields(exists=BOOL, amount=MONEY, reason=string(400))
RECEIPT_OUT = fields(reference=ID)


# ------------------------------------------------------------------ catalog documents


def operation(oid, target, output, *, effect="read", input_schema=NULL, readback=None):
    reconciliation = {"mode": "none"}
    if readback:
        absent = [{"op": "eq", "readback_pointer": "/exists", "expected": lit(False, BOOL)}]
        present = [{"op": "eq", "readback_pointer": "/exists", "expected": lit(True, BOOL)}]
        reconciliation = {
            "mode": "read_back",
            "read_operation": readback,
            "absence_rules": absent,
            "effect_rules": present,
        }
    return {
        "id": oid,
        "target_schema": target,
        "input_schema": input_schema,
        "output_schema": output,
        "effect": effect,
        "idempotency": "target_key" if effect == "write" else "none",
        "reconciliation": reconciliation,
    }


def synthetic_digest(label):
    """A declared, not measured, artifact digest: the connector binary ships in M2."""
    return sha256(f"lotuspond synthetic placeholder: {label}".encode()).hexdigest()


BY_INVOICE = fields(invoice_id=ID)
BY_PO = fields(po_number=ID)
BY_SUPPLIER = fields(supplier_id=ID)
connector = {
    "format_version": 1,
    "kind": "connector",
    "name": "ap-standin",
    "version": VERSION,
    "schemas": [],
    "definition": {
        "artifact_sha256": synthetic_digest("https-json connector 0.1.0"),
        "operations": [
            operation("get_invoice", BY_INVOICE, INVOICE),
            operation("get_purchase_order", BY_PO, ORDER),
            operation("get_goods_receipt", BY_PO, RECEIPT),
            operation("get_supplier_contract", BY_SUPPLIER, CONTRACT),
            operation("list_supplier_invoices", fields(supplier_id=ID, po_number=ID), INVOICES),
            operation("list_credit_notes", BY_SUPPLIER, CREDIT_NOTES),
            operation("get_payment_hold", BY_INVOICE, HOLD_READBACK),
            operation("get_credit_note_request", BY_INVOICE, CREDIT_READBACK),
            operation(
                "set_payment_hold",
                BY_INVOICE,
                RECEIPT_OUT,
                effect="write",
                input_schema=fields(amount=MONEY, reason=string(400)),
                readback="get_payment_hold",
            ),
            operation(
                "request_credit_note",
                BY_INVOICE,
                RECEIPT_OUT,
                effect="write",
                input_schema=fields(supplier_id=ID, amount=MONEY, reason=string(400)),
                readback="get_credit_note_request",
            ),
        ],
    },
}

RETRY = {
    "max_attempts": 3,
    "timeout_seconds": 30,
    "backoff_seconds": 5,
    "retry_codes": ["unavailable"],
}
ONCE = {"max_attempts": 1, "timeout_seconds": 30, "backoff_seconds": 0, "retry_codes": []}
TOOLS = ["get_supplier_contract", "list_supplier_invoices", "list_credit_notes"]
AGENT_LIMITS = {
    "max_steps": 14,
    "timeout_seconds": 240,
    "max_input_tokens": 60000,
    "max_output_tokens": 4000,
    "max_cost_microusd": 250000,
}
MODEL_CONFIGURATION = {"model": "claude-sonnet-5-5", "temperature": 0}
model = {
    "format_version": 1,
    "kind": "model",
    "name": "ap-investigator-model",
    "version": VERSION,
    "schemas": [],
    "definition": {
        "profile": "anthropic-messages",
        "configuration_revision": "1",
        "configuration_sha256": sha256(rfc8785.dumps(MODEL_CONFIGURATION)).hexdigest(),
        "input_schema": INVESTIGATION,
        "output_schema": PROPOSAL,
        "read_tools": [
            {"connector_ref": ref_of(connector), "operation": op, "retry": RETRY} for op in TOOLS
        ],
        "limits": AGENT_LIMITS,
    },
}


def policy(name, independent):
    return {
        "format_version": 1,
        "kind": "policy",
        "name": name,
        "version": VERSION,
        "schemas": [],
        "definition": {
            "approval": "human",
            "independent_review": independent,
            "assignment": "workspace_admins",
            "escalation": None,
            "allowed_operation_classes": [],
            "allowed_capability_classes": [],
        },
    }


clerks = policy("ap-clerks", True)  # the one E04 assignment: workspace admins, independent
hold_policy = policy("ap-payment-hold", False)  # internal and reversible: one admin
credit_policy = policy("ap-credit-note", True)  # reaches the supplier: a second admin

INSTRUCTIONS = """You investigate one accounts-payable exception: an invoice that does \
not match its purchase order and goods receipt within tolerance. You are given the \
invoice, the purchase order (with the quantity already invoiced against it), the goods \
receipt and the computed variance. You may read the supplier's contract, the other \
invoices against the purchase order and the supplier's credit notes with the tools \
provided. You cannot change anything; a person approves any action you propose.

Return:
- exception_class: one of price, quantity, missing_receipt, duplicate, other.
- resolution: hold (place a payment hold on the invoice), request_credit_note (ask the \
supplier for a credit note) or no_action (the difference is justified; a clerk records it).
- amount: the amount to hold or to request as credit, in the invoice currency; 0.00 for \
no_action.
- reasons: at most five short reasons. Each names the tool (or "input") and the field it \
relies on. Do not state anything a document does not show."""


# ------------------------------------------------------------------ the workflow


def triage(status, *, proposal=None, decision="", comment=None):
    p = proposal
    return obj(
        status=lit(status, ID),
        exception_class=step(p, "/exception_class") if p else lit("none", ID),
        resolution=step(p, "/resolution") if p else lit("none", ID),
        amount=step(p, "/amount") if p else lit("0.00", MONEY),
        reasons=step(p, "/reasons")
        if p
        else {"kind": "array", "items": [], "item_schema": data(REASON)},
        clerk_decision=step(decision, "/decision") if decision else lit("", ID),
        comment=step(decision, "/comment") if decision else lit("", string(1000)),
    )


reads = [
    {
        "id": "invoice",
        "kind": "connector_read",
        "connector_ref": "ap",
        "operation": "get_invoice",
        "target": obj(invoice_id=ref("/invoice_id")),
        "input": lit(None, NULL),
        "retry": RETRY,
    },
    {
        "id": "order",
        "kind": "connector_read",
        "connector_ref": "ap",
        "operation": "get_purchase_order",
        "target": obj(po_number=step("invoice", "/po_number")),
        "input": lit(None, NULL),
        "retry": RETRY,
    },
    {
        "id": "receipt",
        "kind": "connector_read",
        "connector_ref": "ap",
        "operation": "get_goods_receipt",
        "target": obj(po_number=step("invoice", "/po_number")),
        "input": lit(None, NULL),
        "retry": RETRY,
    },
    {
        "id": "variance",
        "kind": "transform",
        "mapping": obj(
            price_delta=call(
                "subtract",
                step("invoice", "/total"),
                step("order", "/total"),
                scale=2,
                rounding="reject",
            ),
            quantity_needed=call(
                "add", step("invoice", "/total_quantity"), step("order", "/invoiced_quantity")
            ),
        ),
        "output_schema": VARIANCE,
    },
]


def scaled(factor, value):
    return call("multiply", lit(f"{factor}.00", FACTOR), value, scale=2, rounding="reject")


# 0.5 % with no absolute value: -5 × PO total ≤ 1000 × Δ ≤ 5 × PO total, and nothing
# invoiced beyond what was received (counting what was invoiced against the PO before).
within_tolerance = call(
    "and",
    call(
        "and",
        call(
            "le",
            scaled(-5, step("order", "/total")),
            scaled(1000, step("variance", "/price_delta")),
        ),
        call(
            "le", scaled(1000, step("variance", "/price_delta")), scaled(5, step("order", "/total"))
        ),
    ),
    call("le", step("variance", "/quantity_needed"), step("receipt", "/received_quantity")),
)

investigate = {
    "id": "investigate",
    "kind": "agent",
    "model_ref": "investigator",
    "input": obj(
        invoice=step("invoice"),
        order=step("order"),
        receipt=step("receipt"),
        variance=step("variance"),
    ),
    "instructions": INSTRUCTIONS,
    "output_schema": PROPOSAL,
    "read_tools": [{"connector_ref": "ap", "operation": op, "retry": RETRY} for op in TOOLS],
    "limits": AGENT_LIMITS,
    "retry": ONCE,
}


def clerk_body(sid="clerk"):
    return body(
        [
            {
                "id": sid,
                "kind": "human_task",
                "mode": "form",
                "assignment_policy_ref": "clerks",
                "timeout_seconds": 3 * 86400,
                "form_schema": FORM,
            }
        ],
        triage("clerk_decided", proposal="investigate", decision=sid),
        TRIAGE,
    )


def action_body(prefix, status, operation_id, read_operation, policy_ref, independent, input_):
    intent, review, dispatch, verify = (
        f"{prefix}_{s}" for s in ("intent", "review", "dispatch", "verify")
    )
    target = obj(invoice_id=step("invoice", "/invoice_id"))
    return body(
        [
            {
                "id": intent,
                "kind": "action_intent",
                "connector_ref": "ap",
                "operation": operation_id,
                "target": target,
                "input": input_,
                "policy_ref": policy_ref,
                "approval": "human",
                "independent_review": independent,
                "expiry_seconds": 86400,
            },
            {
                "id": review,
                "kind": "human_task",
                "mode": "review",
                "assignment_policy_ref": policy_ref,
                "timeout_seconds": 86400,
                "intent": step(intent),
            },
            {
                "id": dispatch,
                "kind": "action_dispatch",
                "intent": step(intent),
                "decision": step(review),
                "retry": ONCE,
            },
            {
                "id": verify,
                "kind": "outcome_verification",
                "receipt": step(dispatch),
                "connector_ref": "ap",
                "read_operation": read_operation,
                "target": target,
                "input": lit(None, NULL),
                "rule_id": f"{prefix}-recorded",
                "version": "1",
                "rules": [
                    {"op": "eq", "readback_pointer": "/exists", "expected": lit(True, BOOL)},
                    {
                        "op": "eq",
                        "readback_pointer": "/amount",
                        "expected": step("investigate", "/amount"),
                    },
                ],
            },
        ],
        triage(status, proposal="investigate"),
        TRIAGE,
    )


reason = call(
    "coalesce",
    step("investigate", "/reasons/0/text"),
    lit("No reason was given.", string(400)),
)
hold_body = action_body(
    "hold",
    "held",
    "set_payment_hold",
    "get_payment_hold",
    "hold_approvers",
    False,
    obj(amount=step("investigate", "/amount"), reason=reason),
)
credit_body = action_body(
    "credit",
    "credit_requested",
    "request_credit_note",
    "get_credit_note_request",
    "credit_approvers",
    True,
    obj(
        supplier_id=step("invoice", "/supplier_id"),
        amount=step("investigate", "/amount"),
        reason=reason,
    ),
)


def resolution_is(value):
    return call("eq", step("investigate", "/resolution"), lit(value, ID))


def exception_body(route_hold, route_credit):
    route = {
        "id": "route",
        "kind": "decision",
        "condition": resolution_is("hold"),
        "then_body": route_hold,
        "else_body": body(
            [
                {
                    "id": "route_credit",
                    "kind": "decision",
                    "condition": resolution_is("request_credit_note"),
                    "then_body": route_credit,
                    "else_body": clerk_body(),
                }
            ],
            step("route_credit"),
            TRIAGE,
        ),
    }
    return body([investigate, route], step("route"), TRIAGE)


def workflow(package_id, name, exception, policies):
    matched = {
        "id": "matched",
        "kind": "decision",
        "condition": within_tolerance,
        "then_body": body([], triage("matched"), TRIAGE),
        "else_body": exception,
    }
    return {
        "schema_version": "lotuspond.workflow-package.v2",
        "package_id": package_id,
        "version": VERSION,
        "name": name,
        "input_schema": fields(invoice_id=ID),
        "output_schema": TRIAGE,
        "schemas": [],
        "body": body([*reads, matched], step("matched"), TRIAGE),
        "secret_refs": [],
        "environment": {
            "runtime_contract": "lotuspond.runtime-contract.v1",
            "platforms": [{"os": "linux", "architecture": "amd64"}],
            "required_capabilities": [],
            "destination_classes": [],
            "operation_classes": [],
        },
        "limits": {
            "max_steps": 200,
            "active_seconds": 900,
            "elapsed_seconds": 8 * 86400,
            "retained_waits": 10,
            "max_input_tokens": 60000,
            "max_output_tokens": 4000,
            "max_cost_microusd": 250000,
        },
        "assurance": {
            "evaluation_refs": [],
            "outcome_rule_refs": [],
            "required_classification": "synthetic",
        },
        "models": [{"id": "investigator", "ref": ref_of(model)}],
        "connectors": [{"id": "ap", "ref": ref_of(connector)}],
        "agents": [],
        "subworkflows": [],
        "evaluations": [],
        "policies": [{"id": key, "ref": ref_of(doc)} for key, doc in policies],
        "knowledge": [],
        "artifacts": [],
    }


full = workflow(
    "invoice-exception-triage",
    "Invoice exception triage (synthetic reference)",
    exception_body(hold_body, credit_body),
    [("clerks", clerks), ("hold_approvers", hold_policy), ("credit_approvers", credit_policy)],
)
prefix = workflow(
    "invoice-exception-triage-prefix",
    "Invoice exception triage, before E05 (synthetic reference)",
    exception_body(clerk_body("clerk_hold"), clerk_body("clerk_credit")),
    [("clerks", clerks)],
)


def write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    catalog = [connector, model, clerks, hold_policy, credit_policy]
    plan = compile_package(full, dependencies=[], catalog={"documents": catalog})
    print("full package compiled:", len(plan.steps), "steps")
    plan = compile_package(
        prefix, dependencies=[], catalog={"documents": [connector, model, clerks]}
    )
    print("prefix package compiled:", len(plan.steps), "steps")
    for document in catalog:
        write(OUT / "catalog" / f"{document['name']}.json", document)
    write(OUT / "package.json", full)
    write(OUT / "prefix.json", copy.deepcopy(prefix))
