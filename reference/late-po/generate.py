"""Writes the late purchase order reference package and its catalog documents.

Run from the repository root:
`uv run --frozen --project backend python reference/late-po/generate.py`.

Every document is synthetic (see README.md). The package reads three stand-in systems,
each through its own connector and credential (the ERP, the carrier's tracking service
and the shared inbox), lets an agent propose a new promised date, and dispatches two
reviewed actions: the ERP date change and, when the customer is affected, the customer
update. Both are read back and verified. The package is compiled with the SDK here.
Specification: docs/superpowers/specs/2026-11-14-late-purchase-order-design.md.
"""

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
# Dates are whole days since 1970-01-01: expressions compare numbers, not dates.
DAY = {"kind": "integer", "min": 0, "max": 200_000}
QTY = {"kind": "integer", "min": 0, "max": 1_000_000}
LINE = fields(sku=ID, quantity=QTY)
ORDER = fields(
    po_number=ID,
    supplier_id=ID,
    promised_day=DAY,
    sales_order_id=ID,
    customer_requested_day=DAY,
    lines=array(LINE, 50),
)
SHIPMENT = fields(po_number=ID, shipped=BOOL, status=string(32), estimated_day=DAY)
EVENTS = fields(events=array(fields(day=DAY, code=string(32), text=string(200)), 50))
MESSAGES = fields(messages=array(fields(message_id=ID, day=DAY, text=string(1000)), 20))
COVER = fields(sku=ID, days_of_cover={"kind": "integer", "min": 0, "max": 10_000})
REASON = fields(text=string(400), tool=string(64), field=string(128))
PROPOSAL = fields(new_promised_day=DAY, notify_customer=BOOL, reasons=array(REASON, 5))
INVESTIGATION = fields(order=ORDER, shipment=SHIPMENT)
RECOVERY = fields(status=ID, new_promised_day=DAY, notify_customer=BOOL, reasons=array(REASON, 5))
DATE_READBACK = fields(exists=BOOL, promised_day=DAY, reason=string(400))
UPDATE_READBACK = fields(exists=BOOL, promised_day=DAY, reason=string(400))
RECEIPT_OUT = fields(reference=ID)
BY_PO = fields(po_number=ID)
BY_SKU = fields(sku=ID)
BY_SALES_ORDER = fields(sales_order_id=ID)
CHANGE = fields(promised_day=DAY, reason=string(400))


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


# Each system is its own connector, routes document and credential. The paths are the
# stand-in's (`standin/server.py` ROUTES); a customer's ERP, carrier or inbox would be
# another routes document and so another pinned connector.
ROUTES = {
    "erp": {
        "get_purchase_order": {"method": "GET", "path": "/erp/purchase-orders/{po_number}"},
        "get_stock_cover": {"method": "GET", "path": "/erp/stock/{sku}"},
        "get_promised_date_change": {
            "method": "GET",
            "path": "/erp/purchase-orders/{po_number}/promised-date",
            "input": "ignored",
        },
        "set_promised_date": {
            "method": "POST",
            "path": "/erp/purchase-orders/{po_number}/promised-date",
        },
    },
    "carrier": {
        "get_shipment": {"method": "GET", "path": "/carrier/shipments/{po_number}"},
        "list_tracking_events": {"method": "GET", "path": "/carrier/shipments/{po_number}/events"},
    },
    "inbox": {
        "list_supplier_messages": {"method": "GET", "path": "/inbox/purchase-orders/{po_number}"},
        "get_customer_update": {
            "method": "GET",
            "path": "/inbox/sent/{sales_order_id}",
            "input": "ignored",
        },
        "send_customer_update": {"method": "POST", "path": "/inbox/sent/{sales_order_id}"},
    },
}


def routes_document(system):
    return {
        "format": "lotuspond.https-json.routes.v1",
        "connector": {"name": f"{system}-standin", "version": VERSION},
        "operations": ROUTES[system],
    }


def connector(system, operations):
    return {
        "format_version": 1,
        "kind": "connector",
        "name": f"{system}-standin",
        "version": VERSION,
        "schemas": [],
        "definition": {
            "artifact_sha256": sha256(rfc8785.dumps(routes_document(system))).hexdigest(),
            "operations": operations,
        },
    }


erp = connector(
    "erp",
    [
        operation("get_purchase_order", BY_PO, ORDER),
        operation("get_stock_cover", BY_SKU, COVER),
        # A read-back takes the write's exact target and input (E05 owner decision 18).
        operation("get_promised_date_change", BY_PO, DATE_READBACK, input_schema=CHANGE),
        operation(
            "set_promised_date",
            BY_PO,
            RECEIPT_OUT,
            effect="write",
            input_schema=CHANGE,
            readback="get_promised_date_change",
        ),
    ],
)
carrier = connector(
    "carrier",
    [
        operation("get_shipment", BY_PO, SHIPMENT),
        operation("list_tracking_events", BY_PO, EVENTS),
    ],
)
inbox = connector(
    "inbox",
    [
        operation("list_supplier_messages", BY_PO, MESSAGES),
        operation("get_customer_update", BY_SALES_ORDER, UPDATE_READBACK, input_schema=CHANGE),
        operation(
            "send_customer_update",
            BY_SALES_ORDER,
            RECEIPT_OUT,
            effect="write",
            input_schema=CHANGE,
            readback="get_customer_update",
        ),
    ],
)
CONNECTORS = {"erp": erp, "carrier": carrier, "inbox": inbox}

RETRY = {
    "max_attempts": 3,
    "timeout_seconds": 30,
    "backoff_seconds": 5,
    "retry_codes": ["unavailable"],
}
ONCE = {"max_attempts": 1, "timeout_seconds": 30, "backoff_seconds": 0, "retry_codes": []}
# (package connector id, operation): the agent's read tools, one from each system.
TOOLS = [
    ("inbox", "list_supplier_messages"),
    ("carrier", "list_tracking_events"),
    ("erp", "get_stock_cover"),
]
AGENT_LIMITS = {
    # Also the execution's step budget (E04 takes the smallest): 2 reads, the agent root,
    # 6 model rounds and 8 tool calls is 17.
    "max_steps": 20,
    "timeout_seconds": 600,
    "max_input_tokens": 60000,
    "max_output_tokens": 16000,  # thinking counts as output
    "max_cost_microusd": 20000,
}
# The first reference workflow's model and settings (its decision 3), so both are
# scored the same way: Claude Haiku 5.5 at medium effort, no sampling parameters.
MODEL_CONFIGURATION = {
    "model": "claude-haiku-5-5",
    "effort": "medium",
    "price_microusd_per_mtok": {
        "input": 100000,
        "output": 500000,
        "long_context_above": 100000,
        "long_input": 500000,
        "long_output": 2500000,
    },
}
model = {
    "format_version": 1,
    "kind": "model",
    "name": "late-po-investigator-model",
    "version": VERSION,
    "schemas": [],
    "definition": {
        "profile": "anthropic-messages",
        "configuration_revision": "1",
        "configuration_sha256": sha256(rfc8785.dumps(MODEL_CONFIGURATION)).hexdigest(),
        "input_schema": INVESTIGATION,
        "output_schema": PROPOSAL,
        "read_tools": [
            {"connector_ref": ref_of(CONNECTORS[system]), "operation": op, "retry": RETRY}
            for system, op in TOOLS
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


planners = policy("late-po-planners", False)  # internal and reversible: one planner
customer_messages = policy("late-po-customer-messages", True)  # reaches a customer: two

INSTRUCTIONS = """You handle one late purchase order: the carrier's estimate is after \
the date promised to the business, or nothing has shipped. You are given the order \
(with the customer sales order it serves and the day the customer asked for) and the \
carrier's shipment. Days are whole days since 1970-01-01. You may read the supplier's \
messages about the order, the carrier's tracking events and the stock cover of each \
line with the tools provided. You cannot change anything; a person approves any action \
you propose.

Return:
- new_promised_day: the earliest day the goods can realistically arrive, from the \
supplier's confirmed date or the carrier's events. Never earlier than the carrier's \
estimate when there is one.
- notify_customer: true when new_promised_day is after the customer's requested day.
- reasons: at most five short reasons. Each names the tool (or "input") and the field it \
relies on. Do not state anything a document does not show."""


# ------------------------------------------------------------------ the workflow


def recovery(status, *, proposal=None):
    if proposal is None:
        return obj(
            status=lit(status, ID),
            new_promised_day=step("order", "/promised_day"),
            notify_customer=lit(False, BOOL),
            reasons={"kind": "array", "items": [], "item_schema": data(REASON)},
        )
    return obj(
        status=lit(status, ID),
        new_promised_day=step(proposal, "/new_promised_day"),
        notify_customer=step(proposal, "/notify_customer"),
        reasons=step(proposal, "/reasons"),
    )


reads = [
    {
        "id": "order",
        "kind": "connector_read",
        "connector_ref": "erp",
        "operation": "get_purchase_order",
        "target": obj(po_number=ref("/po_number")),
        "input": lit(None, NULL),
        "retry": RETRY,
    },
    {
        "id": "shipment",
        "kind": "connector_read",
        "connector_ref": "carrier",
        "operation": "get_shipment",
        "target": obj(po_number=ref("/po_number")),
        "input": lit(None, NULL),
        "retry": RETRY,
    },
]

# Shipped, and the carrier expects it on or before the promised day.
on_time = call(
    "and",
    step("shipment", "/shipped"),
    call("le", step("shipment", "/estimated_day"), step("order", "/promised_day")),
)

investigate = {
    "id": "investigate",
    "kind": "agent",
    "model_ref": "investigator",
    "input": obj(order=step("order"), shipment=step("shipment")),
    "instructions": INSTRUCTIONS,
    "output_schema": PROPOSAL,
    "read_tools": [
        {"connector_ref": system, "operation": op, "retry": RETRY} for system, op in TOOLS
    ],
    "limits": AGENT_LIMITS,
    "retry": ONCE,
}

reason = call(
    "coalesce",
    step("investigate", "/reasons/0/text"),
    lit("No reason was given.", string(400)),
)
change = obj(promised_day=step("investigate", "/new_promised_day"), reason=reason)


def action(prefix, system, operation_id, read_operation, policy_ref, independent, target):
    intent, review, dispatch, verify = (
        f"{prefix}_{s}" for s in ("intent", "review", "dispatch", "verify")
    )
    return [
        {
            "id": intent,
            "kind": "action_intent",
            "connector_ref": system,
            "operation": operation_id,
            "target": target,
            "input": change,
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
            "connector_ref": system,
            "read_operation": read_operation,
            "target": target,
            "input": change,
            "rule_id": f"{prefix}-recorded",
            "version": "1",
            "rules": [
                {"op": "eq", "readback_pointer": "/exists", "expected": lit(True, BOOL)},
                {
                    "op": "eq",
                    "readback_pointer": "/promised_day",
                    "expected": step("investigate", "/new_promised_day"),
                },
            ],
        },
    ]


reschedule = action(
    "reschedule",
    "erp",
    "set_promised_date",
    "get_promised_date_change",
    "planners",
    False,
    obj(po_number=step("order", "/po_number")),
)
notify = action(
    "notify",
    "inbox",
    "send_customer_update",
    "get_customer_update",
    "customer_messages",
    True,
    obj(sales_order_id=step("order", "/sales_order_id")),
)
tell = {
    "id": "tell",
    "kind": "decision",
    "condition": step("investigate", "/notify_customer"),
    "then_body": body(
        notify, recovery("rescheduled_and_notified", proposal="investigate"), RECOVERY
    ),
    "else_body": body([], recovery("rescheduled", proposal="investigate"), RECOVERY),
}
late_body = body([investigate, *reschedule, tell], step("tell"), RECOVERY)

package = {
    "schema_version": "lotuspond.workflow-package.v2",
    "package_id": "late-purchase-order-recovery",
    "version": VERSION,
    "name": "Late purchase order recovery (synthetic reference)",
    "input_schema": fields(po_number=ID),
    "output_schema": RECOVERY,
    "schemas": [],
    "body": body(
        [
            *reads,
            {
                "id": "on_time",
                "kind": "decision",
                "condition": on_time,
                "then_body": body([], recovery("on_time"), RECOVERY),
                "else_body": late_body,
            },
        ],
        step("on_time"),
        RECOVERY,
    ),
    # One bearer token per system: each connection binds exactly its own secret.
    "secret_refs": ["erp_token", "carrier_token", "inbox_token"],
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
        "elapsed_seconds": 3 * 86400,
        "retained_waits": 10,
        "max_input_tokens": 60000,
        "max_output_tokens": 16000,
        "max_cost_microusd": 20000,
    },
    "assurance": {
        "evaluation_refs": [],
        "outcome_rule_refs": [],
        "required_classification": "synthetic",
    },
    "models": [{"id": "investigator", "ref": ref_of(model)}],
    "connectors": [
        {"id": system, "ref": ref_of(doc), "credential_ref": f"{system}_token"}
        for system, doc in CONNECTORS.items()
    ],
    "agents": [],
    "subworkflows": [],
    "evaluations": [],
    "policies": [
        {"id": "planners", "ref": ref_of(planners)},
        {"id": "customer_messages", "ref": ref_of(customer_messages)},
    ],
    "knowledge": [],
    "artifacts": [],
}

CATALOG = [erp, carrier, inbox, model, planners, customer_messages]


def write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    plan = compile_package(package, dependencies=[], catalog={"documents": CATALOG})
    print("package compiled:", len(plan.steps), "steps")
    for document in CATALOG:
        write(OUT / "catalog" / f"{document['name']}.json", document)
    for system in ROUTES:
        write(OUT / "catalog" / f"{system}-standin.routes.json", routes_document(system))
    write(OUT / "package.json", package)
