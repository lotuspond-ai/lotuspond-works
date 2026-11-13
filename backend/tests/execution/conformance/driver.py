"""Task 13: a customer and an operator of one world, over the public API.

Everything a case does goes through HTTP routes, except the setup E04 does not own,
which is written as rows and labelled so:

- projects and environments (E01 has no route for them);
- operator provisioning of the scheduler cursor and capacity rows (Task 14's
  operations procedure);
- the pool policy and connection for the catalog's `connector-fixture`, which E03's
  installed catalog does not know, so its routes cannot create them.
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx
import world as worlds
from processes import catalog_ref

_HERE = Path(__file__).resolve().parent
# Copied outside the checkout (installed acceptance), the fixtures sit beside this file.
_COPIED = _HERE / "fixtures"
CONTRACTS = (
    _COPIED
    if (_COPIED / "catalog.json").exists()
    else _HERE.parents[3] / "contracts/examples/executable-v2"
)
FLOWS = (
    _COPIED / "control-flow"
    if (_COPIED / "control-flow").exists()
    else _HERE.parent / "fixtures/control-flow"
)
# E05: the synthetic target connector, its policies and the reviewed-action package.
ACTIONS = (
    _COPIED / "actions" if (_COPIED / "actions").exists() else _HERE.parent / "fixtures/actions"
)


def action_document(name, actions=ACTIONS):
    return json.loads((actions / f"{name}.json").read_text())


def _flag(value):
    return {"kind": "literal", "schema": {"kind": "boolean"}, "value": value}


def decisive_connector(actions=ACTIONS):
    """The synthetic target connector with rules that can tell present from absent.

    The shared fixture's `present` rules test only that `/present` exists, so every
    read-back matches both its effect and absence rules (undecidable) and any read
    verifies. Here `/present` true means the effect is present, false that it is absent.
    """
    connector = action_document("connector", actions)
    submit = next(o for o in connector["definition"]["operations"] if o["id"] == "submit")
    submit["reconciliation"]["effect_rules"] = [
        {"op": "eq", "readback_pointer": "/present", "expected": _flag(True)}
    ]
    submit["reconciliation"]["absence_rules"] = [
        {"op": "eq", "readback_pointer": "/present", "expected": _flag(False)}
    ]
    # ACT-10: the same write without a target key: never resent, absent only once its
    # stop is confirmed.
    once = json.loads(json.dumps(submit))
    once.update(id="submit_once", idempotency="none")
    connector["definition"]["operations"].append(once)
    return connector


def decisive(package, connector):
    """Pin `connector` and verify that `/present` is true, not merely that it exists."""
    from lotuspond_sdk.executable.catalog import catalog_document_digest

    package["connectors"][0]["ref"]["sha256"] = catalog_document_digest(connector)
    for step in package["body"]["steps"]:
        if step["kind"] == "outcome_verification":
            step["rules"] = [{"op": "eq", "readback_pointer": "/present", "expected": _flag(True)}]
    return package


def reviewed_action(record_id, *, max_attempts=1, actions=ACTIONS):
    """E05's reviewed-action package (intent, review, dispatch, verify) for one record,
    over `decisive_connector()`."""
    package = json.loads(
        json.dumps(action_document("reviewed_action", actions)).replace("R-100", record_id)
    )
    package = decisive(package, decisive_connector(actions))
    package["package_id"] = f"e05-{record_id.lower()}"
    # Room for read-backs on a loaded host: each attempt is charged at least its
    # reservation, so five read-backs and a verification outrun the fixture's 100 s.
    package["limits"].update(active_seconds=1000, elapsed_seconds=3600)
    retry = package["body"]["steps"][2]["retry"]
    retry["max_attempts"] = max_attempts
    if max_attempts > 1:
        retry["retry_codes"] = ["timeout_before_dispatch"]
    return package


def catalog_documents(contracts=CONTRACTS):
    return json.loads((contracts / "catalog.json").read_text())["documents"]


def flow(name, flows=FLOWS):
    return json.loads((flows / f"{name}.json").read_text())


class Refused(AssertionError):
    def __init__(self, response):
        self.status = response.status_code
        try:
            self.code = response.json().get("error")
        except ValueError:
            self.code = None
        super().__init__(f"{self.status} {self.code}")


class Customer:
    """One signed-in person in one workspace: their session over CA-verified TLS."""

    def __init__(self, base_url, ca, origin, cookie, workspace, prefix):
        self.client = httpx.Client(base_url=base_url, verify=verified(ca), timeout=30)
        self.headers = {"origin": origin, "cookie": cookie, "x-lotuspond-workspace": workspace}
        self.workspace, self.prefix = workspace, prefix

    def call(self, method, path, body=None, *, key=None, expect=(200, 201), retry=True):
        headers = dict(self.headers)
        if method != "GET":
            headers["idempotency-key"] = key or uuid4().hex
        url = self.prefix + path if path.startswith("/") else path
        for attempt in range(20):
            response = self.client.request(method, url, json=body, headers=headers)
            # The shared fleet lookup admits one call at a time (a recorded Task 12
            # limitation); customers retry its transient refusal.
            busy = response.status_code == 503 and _code(response) == "authority_unavailable"
            if not (retry and busy) or attempt == 19:
                break
            time.sleep(0.1)
        if response.status_code not in expect:
            raise Refused(response)
        return response.json() if response.content else None

    def close(self):
        self.client.close()


def verified(ca):
    """TLS that trusts exactly the world's synthetic CA."""
    import ssl

    return ssl.create_default_context(cafile=str(ca))


def _code(response):
    try:
        return response.json().get("error")
    except ValueError:
        return None


class Operator:
    """Sets up one world's tenant and drives it as its owner."""

    def __init__(
        self,
        world,
        processes,
        *,
        contracts=CONTRACTS,
        flows=FLOWS,
        connector=None,
        destinations=(),
        secret_binding=None,
        actions=None,
        connections=None,
    ):
        self.world, self.processes = world, processes
        # Several connectors, each through its own connection and secret (a package that
        # reads more than one system): a list of {"connector", "destinations",
        # "secret_binding", "writes"}. The first is `connector` for everything else.
        self.connections = list(connections or ())
        if self.connections:
            connector = self.connections[0]["connector"]
        self.contracts, self.flows = contracts, flows
        self.documents = catalog_documents(contracts)
        # The connector each pool's connection and each runtime serve; its read
        # operations are the connection's. The catalog fixture connector by default.
        self.connector = connector or self.documents[0]
        self.read_operations = tuple(
            op["id"] for op in self.connector["definition"]["operations"] if op["effect"] == "read"
        )
        # The connection's destinations and, when named, one bound secret reference.
        self.destinations, self.secret_binding = tuple(destinations), secret_binding
        # E05: the synthetic target connector's pin, when runtimes serve its writes. The
        # connection then admits its writes too, and enrollments admit the connector.
        self.actions = actions
        # A deployment refuses a connection allowing a write its package never makes:
        # the connection allows the reads and the write(s) named (`submit` by default).
        self.operations = (
            self.read_operations + tuple(actions.get("writes", ("submit",)))
            if actions is not None
            else self.read_operations
        )
        self.published = {}
        self.deployed = {}

    @property
    def base_url(self):
        return f"https://localhost:{self.processes.api_port}"

    # ---------------------------------------------------------------- tenant setup

    def register(self, email=None, workspace="Conformance"):
        client = httpx.Client(
            base_url=self.base_url, verify=verified(self.processes.ca), timeout=30
        )
        try:
            response = client.post(
                "/api/v1/auth/register",
                json={
                    "email": email or f"owner-{uuid4().hex[:12]}@example.com",
                    "name": "Owner",
                    "password": "synthetic-conformance-password",
                    "account_type": "organization",
                    "workspace": workspace,
                },
                headers={"origin": self.world.config["origin"]},
            )
        finally:
            client.close()
        if response.status_code != 201:
            raise Refused(response)
        cookie = "lotuspond_session=" + response.cookies["lotuspond_session"]
        return cookie, response.json()["workspaceId"]

    def tenant(self, engine, *, capacity=5, label="Conformance"):
        """A workspace, project and environment, provisioned for execution."""
        cookie, workspace = self.register(workspace=label)
        return self._environment(engine, cookie, workspace, capacity, label)

    def environment(self, engine, customer, *, capacity=5, label="Conformance"):
        """Another project and environment in this customer's workspace, with its own
        capacity, pool and connection: isolation without another registration."""
        cookie = customer.headers["cookie"]
        return self._environment(engine, cookie, customer.workspace, capacity, label)

    def _environment(self, engine, cookie, workspace, capacity, label):
        from sqlalchemy.orm import Session

        from lotuspond import models

        project, environment = str(uuid4()), str(uuid4())
        with Session(engine) as db, db.begin():
            db.add(models.Project(id=project, workspace_id=workspace, name=label))
            db.flush()
            db.add(
                models.Environment(
                    id=environment,
                    workspace_id=workspace,
                    project_id=project,
                    name=label,
                    kind="local",
                )
            )
            db.flush()
            # Operator provisioning (Task 14's procedure): capacity for this environment.
            db.add(
                models.ExecutionCapacity(
                    workspace_id=workspace,
                    project_id=project,
                    environment_id=environment,
                    active_limit=capacity,
                )
            )
        prefix = f"/api/v1/projects/{project}/environments/{environment}"
        customer = Customer(
            self.base_url, self.processes.ca, self.world.config["origin"], cookie, workspace, prefix
        )
        customer.scope = {
            "workspace_id": workspace,
            "project_id": project,
            "environment_id": environment,
        }
        customer.pool_id, customer.connection = self.pool(engine, customer.scope)
        return customer

    def pool(self, engine, scope, *, concurrency=5):
        """A runtime pool whose policy admits the catalog connector through one connection."""
        from lotuspond_runtime.protocol import (
            ConnectionConfig,
            ConnectorPin,
            DataHandling,
            DestinationRule,
            PoolPolicy,
            SecretPin,
            canonical_connection_json,
            canonical_pool_json,
            connection_digest,
            pool_digest,
        )
        from sqlalchemy.orm import Session

        from lotuspond import models

        entries = self.connections or [
            {
                "connector": self.connector,
                "destinations": self.destinations,
                "secret_binding": self.secret_binding,
                "operations": self.operations,
            }
        ]
        pool_id = str(uuid4())
        with Session(engine) as db, db.begin():
            db.add(models.RuntimePool(**scope, id=pool_id, name="Conformance", status="active"))
            pins, views = [], {}
            for entry in entries:
                connector = entry["connector"]
                pin = ConnectorPin(
                    name=connector["name"],
                    version=connector["version"],
                    artifact_sha256=connector["definition"]["artifact_sha256"],
                )
                operations = entry.get("operations") or tuple(
                    op["id"]
                    for op in connector["definition"]["operations"]
                    if op["effect"] == "read" or op["id"] in entry.get("writes", ())
                )
                connection = models.Connection(
                    **scope, name=f"{connector['name']}-connection", status="active"
                )
                db.add(connection)
                secrets = ()
                if entry.get("secret_binding") is not None:
                    secret = models.SecretReference(
                        **scope, binding_id=entry["secret_binding"], version=1, status="active"
                    )
                    db.add(secret)
                    db.flush()
                    secrets = (SecretPin(reference_id=secret.id, version=1),)
                db.flush()
                config = ConnectionConfig(
                    connector=pin,
                    operations=tuple(operations),
                    destinations=tuple(
                        DestinationRule.model_validate(d) for d in entry.get("destinations", ())
                    ),
                    secrets=secrets,
                    # The longest call a connection allows (E03's 30 s bound), so a
                    # lease renewal (every 20 s) happens inside one read.
                    data_handling=DataHandling(timeout_ms=30000),
                )
                db.add(
                    models.ConnectionVersion(
                        **scope,
                        connection_id=connection.id,
                        version=1,
                        canonical_config=canonical_connection_json(config),
                        config_digest=connection_digest(config),
                    )
                )
                for secret_pin in secrets:
                    db.add(
                        models.ConnectionVersionSecret(
                            **scope,
                            connection_id=connection.id,
                            connection_version=1,
                            secret_reference_id=secret_pin.reference_id,
                            secret_version=secret_pin.version,
                        )
                    )
                pins.append(pin)
                views[connector["name"]] = {
                    "id": connection.id,
                    "version": 1,
                    "secrets": [p.model_dump(mode="json") for p in secrets],
                }
            policy = PoolPolicy(
                connectors=tuple(pins),
                platforms=("linux",),
                connection_ids=tuple(view["id"] for view in views.values()),
                max_instances=16,
                max_concurrent_invocations=concurrency,
            )
            db.add(
                models.RuntimePoolPolicy(
                    **scope,
                    pool_id=pool_id,
                    version=1,
                    canonical_policy=canonical_pool_json(policy),
                    policy_digest=pool_digest(policy),
                )
            )
            db.flush()
            for view in views.values():
                db.add(
                    models.RuntimePoolPolicyConnection(
                        **scope, pool_id=pool_id, policy_version=1, connection_id=view["id"]
                    )
                )
            db.get(models.RuntimePool, pool_id).current_policy_version = 1
        if self.connections:
            return pool_id, {"by_connector": views, **next(iter(views.values()))}
        return pool_id, next(iter(views.values()))

    def enrollment(self, customer) -> str:
        issued = customer.call(
            "POST",
            f"/runtime-pools/{customer.pool_id}/enrollments",
            {
                "platform": "linux",
                "connectors": [] if self.actions is None else [self.actions["pin"]],
            },
        )
        return issued["token"]

    def runtime(self, customer, name, *, model_document=None, start=True, activity=None, **scripts):
        connector, model = self.connector, model_document or self.model_document()
        return self.processes.start_runtime(
            name,
            self.enrollment(customer),
            operations=list(self.read_operations),
            activity=activity,
            dependencies={
                "connector": catalog_ref(connector),
                "installed": {
                    "name": connector["name"],
                    "version": connector["version"],
                    "artifact_sha256": connector["definition"]["artifact_sha256"],
                },
                "model": catalog_ref(model),
            },
            start=start,
            actions=None if self.actions is None else self._runtime_actions(),
            **scripts,
        )

    def _runtime_actions(self):
        definition = self.connector["definition"]
        return {
            "pin": self.actions["pin"],
            "connector_ref": catalog_ref(self.connector),
            "read_operations": [
                op["id"] for op in definition["operations"] if op["effect"] == "read"
            ],
            "write_operations": {
                op["id"]: op["idempotency"]
                for op in definition["operations"]
                if op["effect"] == "write"
            },
        }

    def member(self, customer, role, person=None):
        """Another person in the customer's workspace with this role: a real session
        from registration, made a member by operator provisioning. `person` (an earlier
        result) joins this workspace instead of registering again: the API rate-limits
        sign-ups."""
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from lotuspond import models

        if person is not None:
            cookie, user_id = person.headers["cookie"], person.user_id
            with Session(self.world.engine) as db, db.begin():
                db.add(
                    models.Membership(workspace_id=customer.workspace, user_id=user_id, role=role)
                )
            joined = Customer(
                self.base_url,
                self.processes.ca,
                self.world.config["origin"],
                cookie,
                customer.workspace,
                customer.prefix,
            )
            joined.user_id, joined.role, joined.scope = user_id, role, customer.scope
            return joined
        cookie, own = self.register(workspace=f"{role} home")
        with Session(self.world.engine) as db, db.begin():
            user_id = db.scalar(
                select(models.Membership.user_id).where(models.Membership.workspace_id == own)
            )
            db.add(models.Membership(workspace_id=customer.workspace, user_id=user_id, role=role))
        person = Customer(
            self.base_url,
            self.processes.ca,
            self.world.config["origin"],
            cookie,
            customer.workspace,
            customer.prefix,
        )
        person.user_id, person.role, person.scope = user_id, role, customer.scope
        return person

    def demand(self, active_ms):
        """Each attempt's reserved active time from now on (a lease never outlives it)."""
        demand = {**self.world.state("entitlement")["demand"], "active_ms": active_ms}
        self.world.update("entitlement", demand=demand)

    def hand(self, customer, name, **scripts):
        """An enrolled runtime instance the test drives itself (no worker process)."""
        from processes import Hand

        enrolled = self.runtime(customer, name, start=False, **scripts)
        return Hand(self.processes, enrolled)

    def revoke(self, customer, instance_id):
        return customer.call("POST", f"/runtime-instances/{instance_id}/revoke", {})

    def model_document(self):
        return json.loads((self.flows / "agent_tools.model.json").read_text())

    # ---------------------------------------------------------------- publication

    def published_once(self, customer, document, dependency_ids=()):
        """A catalog document or package published once per environment and content."""
        cache_key = (customer.prefix, json.dumps(document, sort_keys=True))
        if cache_key not in self.published:
            self.published[cache_key] = self.publish(customer, document, dependency_ids)
        return self.published[cache_key]

    def publish(self, customer, document, dependency_ids=()):
        from lotuspond_sdk.executable.catalog import catalog_document_digest
        from lotuspond_sdk.executable.signing import sign_executable_package

        is_workflow = "schema_version" in document
        if is_workflow:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

            key = Ed25519PrivateKey.from_private_bytes(
                bytes.fromhex(self.world.state("keys")["package"])
            )
            signature = sign_executable_package(document, key).model_dump(mode="json")
        else:
            worlds.admit_catalog(self.world, catalog_document_digest(document))
            signature = None
        return customer.call(
            "POST",
            "/executable-packages",
            {"package": document, "dependency_ids": list(dependency_ids), "signature": signature},
        )

    def revise(self, customer, deployment_id, name, *, dependencies=(), version="1.0.1"):
        """A new revision of an existing deployment, from a re-versioned package; enabled."""
        package = copy.deepcopy(flow(name, self.flows))
        package["version"] = version
        return self.deploy(
            customer,
            name,
            package=package,
            dependencies=dependencies,
            deployment_id=deployment_id,
            label=f"{name}@{version}",
        )

    def deploy(
        self,
        customer,
        name,
        *,
        package=None,
        dependencies=(),
        capacity=1,
        label=None,
        deployment_id=None,
        knowledge=None,
    ):
        """Publish a control-flow fixture with its dependencies; deploy and enable it.

        `dependencies` are catalog documents or fixture names; a connector document
        is bound to the pool's connection, and each `knowledge` entry's reference to
        the registered context source `(source_id, version)` named by its ID.
        """
        label = label or name
        cache_key = (customer.prefix, label)
        if cache_key in self.deployed:
            return self.deployed[cache_key]
        package = copy.deepcopy(package or flow(name, self.flows))
        published, documents = [], []
        for dependency in dependencies:
            document = flow(dependency, self.flows) if isinstance(dependency, str) else dependency
            definition = document.get("definition", {})
            standalone = "schema_version" not in document and not (
                definition.get("model_ref") or definition.get("read_tools")
            )
            closure = () if standalone else tuple(item["id"] for item in published)
            published.append(self.published_once(customer, document, closure))
            documents.append(document)
        publication = self.publish(customer, package, tuple(item["id"] for item in published))
        refs = [
            entry["ref"]
            for family in ("models", "connectors", "policies", "subworkflows", "agents")
            for entry in package.get(family, ())
        ]
        bindings = []
        for ref in refs:
            binding = {"dependency": ref}
            if ref["kind"] == "connector":
                # Each connector through its own connection when there are several.
                connection = customer.connection.get("by_connector", {}).get(
                    ref["name"], customer.connection
                )
                binding.update(
                    connection_id=connection["id"],
                    connection_version=connection["version"],
                )
            bindings.append(binding)
        for entry in package.get("knowledge", ()):
            source_id, version = (knowledge or {})[entry["id"]]
            bindings.append(
                {"dependency": entry["ref"], "source_id": source_id, "source_version": version}
            )
        limits = {
            **package["limits"],
            "active_capacity": capacity,
            "pending_capacity": 1000,
        }
        view = customer.call(
            "POST",
            "/deployments",
            {
                "deployment_id": deployment_id,
                "publication_id": publication["id"],
                "pool_id": customer.pool_id,
                "release_id": None,
                "configuration": {"bindings": bindings, "limits": limits},
            },
        )
        customer.call(
            "POST",
            f"/deployments/{view['id']}/revisions/{view['revision_id']}/status",
            {"status": "enabled"},
        )
        self.deployed[cache_key] = view["revision_id"]
        return view["revision_id"]

    def context_source(self, customer, content, citation, *, classification="synthetic"):
        """Register a knowledge source through its route; returns `(source_id, 1)`.

        `last_context` keeps the route's whole answer, with its artifact handles.
        """
        from sqlalchemy import text
        from sqlalchemy.orm import Session

        with Session(self.world.engine) as db:
            now = db.scalar(
                text("SELECT floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint")
            )
        view = customer.call(
            "POST",
            "/context-sources",
            {
                "content": content,
                "citation": citation,
                "classification": classification,
                "observed_at_ms": now - 1000,
                "expires_at_ms": now + 3_600_000,
                "retain_until_ms": now + 3_600_000,
                "access_policy_id": "conformance-access",
                "key_id": "conformance",
            },
        )
        self.last_context = view
        return view["source_id"], view["version"]

    # ---------------------------------------------------------------- executions

    def start(self, customer, revision, value=None, *, key=None):
        return customer.call(
            "POST",
            "/executions",
            {"deployment_revision_id": revision, "input": value},
            key=key,
        )

    def execution(self, customer, execution_id):
        return customer.call("GET", f"/executions/{execution_id}")

    def settled(self, customer, execution_id, states, seconds=60):
        """Wait in real time until the execution reaches one of these observed states."""
        deadline = time.monotonic() + seconds
        while True:
            view = self.execution(customer, execution_id)
            if view["state"] in states:
                return view
            if time.monotonic() > deadline:
                raise AssertionError(f"execution stayed {view['state']}")
            time.sleep(0.2)

    # ---------------------------------------------------------------- waits and controls

    def waits(self, customer, execution_id):
        return customer.call("GET", f"/executions/{execution_id}/waits")["items"]

    def open_wait(self, customer, execution_id, path, seconds=60):
        """The open wait at this occurrence path, once it can be signalled.

        E04 finding 2 (fixed in E05 Task 5): a wait is listed open only once the
        capacity step has retained it, and only then can it be signalled; a signal
        before that is a 409 to retry. So a listed open wait is ready.
        """
        deadline = time.monotonic() + seconds
        while True:
            for item in self.waits(customer, execution_id):
                if item["occurrence_path"] == path and item["state"] == "open":
                    return item
            if time.monotonic() > deadline:
                raise AssertionError(f"no open wait at {path}")
            time.sleep(0.2)

    def signal(self, customer, execution_id, wait_id, signal_id, payload, *, expect=(200,)):
        return customer.call(
            "POST",
            f"/executions/{execution_id}/signals",
            {"signal_id": signal_id, "wait_id": wait_id, "payload": payload},
            expect=expect,
        )

    def control(self, customer, execution_id, action, request_id=None, *, expect=(200,)):
        return customer.call(
            "POST",
            f"/executions/{execution_id}/control",
            {"request_id": request_id or f"{action}-{uuid4().hex[:8]}", "action": action},
            expect=expect,
        )

    def events(self, customer, execution_id):
        items, after = [], None
        while True:
            query = "" if after is None else f"?after_sequence={after}"
            page = customer.call("GET", f"/executions/{execution_id}/events{query}")
            items += page["items"]
            after = page["next_after_sequence"]
            if after is None:
                return items


def form_package(flows=FLOWS, *, timeout_seconds=3600):
    """`waits_fast` reduced to its human form: one review wait, nothing else."""
    package = copy.deepcopy(flow("waits_fast", flows))
    package["package_id"] = f"form-only-{timeout_seconds}"
    review = next(step for step in package["body"]["steps"] if step["id"] == "review")
    review["timeout_seconds"] = timeout_seconds
    package["body"]["steps"] = [review]
    package["body"]["result"] = {
        "kind": "ref",
        "pointer": "",
        "source": {"id": "review", "kind": "step"},
    }
    package["body"]["result_schema"] = {"kind": "data", "schema": review["form_schema"]}
    package["output_schema"] = review["form_schema"]
    return package


def decoded_events(world, execution_id):
    """Every event of one execution with its encrypted artifact opened (test-side keys)."""
    import json as _json

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from lotuspond.models import ExecutionEvent

    graph = world.graph()
    with Session(world.engine) as db:
        rows = list(
            db.scalars(
                select(ExecutionEvent)
                .where(ExecutionEvent.execution_id == execution_id)
                .order_by(ExecutionEvent.sequence)
            )
        )
        decoded = []
        for row in rows:
            scope = _scope_of(row)
            content = graph.store._get_internal(
                db,
                scope,
                {
                    "id": row.event_artifact_id,
                    "version": row.event_artifact_version,
                    "sha256": row.digest,
                },
            )
            decoded.append((row, _json.loads(content)))
        return decoded


def _scope_of(row):
    from lotuspond_runtime.protocol import Scope

    return Scope(
        workspace_id=row.workspace_id, project_id=row.project_id, environment_id=row.environment_id
    )


def external_waits_package(expiries, flows=FLOWS):
    """`waits_fast`'s external wait, repeated: one wait per expiry, in sequence."""
    package = copy.deepcopy(flow("waits_fast", flows))
    callback = next(step for step in package["body"]["steps"] if step["id"] == "callback")
    steps = []
    for index, expiry in enumerate(expiries):
        step = copy.deepcopy(callback)
        step["id"], step["expiry_seconds"] = f"signal{index}", expiry
        steps.append(step)
    package["package_id"] = "external-" + "-".join(str(e) for e in expiries)
    package["body"]["steps"] = steps
    last = steps[-1]["id"]
    package["body"]["result"] = {
        "kind": "ref",
        "pointer": "",
        "source": {"id": last, "kind": "step"},
    }
    package["body"]["result_schema"] = {"kind": "data", "schema": {"kind": "integer"}}
    package["output_schema"] = {"kind": "integer"}
    return package


def diagnose(world, execution_id):
    """Every attempt of one execution with its decoded result, for failure messages."""
    import json as _json

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from lotuspond.models import Execution, ExecutionAttempt

    graph = world.graph()
    out = []
    with Session(world.engine) as db:
        execution = db.get(Execution, execution_id)
        out.append(("execution", execution.state, execution.reason))
        for row in db.scalars(
            select(ExecutionAttempt)
            .where(ExecutionAttempt.execution_id == execution_id)
            .order_by(ExecutionAttempt.ordinal)
        ):
            result = None
            if row.result_artifact_id is not None:
                try:
                    result = _json.loads(
                        graph.store._get_internal(
                            db,
                            _scope_of(row),
                            {
                                "id": row.result_artifact_id,
                                "version": row.result_artifact_version,
                                "sha256": row.result_sha256,
                            },
                        )
                    )
                except Exception as error:  # noqa: BLE001 - diagnostics only
                    result = type(error).__name__
            out.append((row.ordinal, row.state, row.parent_attempt_id is not None, result))
    return out


def owner_actor(world, customer, operation):
    """The workspace owner's current authority, for a public service call."""
    from lotuspond_runtime.protocol import Scope
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from lotuspond.execution.authorization import authorize_execution
    from lotuspond.models import Membership
    from lotuspond.security import Principal

    with Session(world.engine) as db, db.begin():
        user_id = db.scalar(
            select(Membership.user_id).where(
                Membership.workspace_id == customer.workspace, Membership.role == "owner"
            )
        )
        principal = Principal(user_id, customer.workspace, "owner", user_id)
        return authorize_execution(db, principal, Scope(**customer.scope), operation)


def reconcile_from_journal(world, customer, runtime, execution_id):
    """An owner reconciles the uncertain root attempt with its runtime's journal entry.

    The runtime must have ended (its journal is opened here). The entry must exist:
    evidence is never invented. A started call without an outcome is reported as
    unknown usage, which is what the journal shows. Returns the entry used.

    This runs in the test process, as the owner, through the reconciliation service:
    no route accepts a runtime's journal evidence yet (recorded in the validation
    document).
    """
    import time as _time

    from lotuspond_runtime.activity_protocol import HistoricalObservation
    from lotuspond_runtime.activity_state import InvocationJournal
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from lotuspond.models import ExecutionActivityDelivery, ExecutionAttempt

    with Session(world.engine) as db:
        attempt = db.scalar(
            select(ExecutionAttempt).where(
                ExecutionAttempt.execution_id == execution_id,
                ExecutionAttempt.state == "uncertain",
                ExecutionAttempt.parent_attempt_id.is_(None),
            )
        )
        delivery = db.scalar(
            select(ExecutionActivityDelivery).where(
                ExecutionActivityDelivery.attempt_id == attempt.id
            )
        )
    journal = InvocationJournal(runtime["home"] / "journal")
    try:
        entry = journal.get(attempt.id)
    finally:
        journal.close()
    if entry is None:
        raise AssertionError("the runtime's journal has no entry for the uncertain attempt")
    outcome = entry.get("outcome") or {}
    unknown = {
        "input_tokens": None,
        "output_tokens": None,
        "cost_microusd": None,
        "completed_at_ms": _time.time_ns() // 1_000_000 - 1000,
    }
    observation = HistoricalObservation(
        observation_id="journal-" + attempt.id[:8],
        attempt_id=attempt.id,
        # A delivery made after a backup is in the journal only, not the restored rows.
        delivery_sha256=delivery.digest if delivery is not None else entry["delivery_sha256"],
        result=None,
        code=outcome.get("code", "unavailable"),
        usage=outcome.get("usage") or unknown,
    )
    graph = world.graph()
    actor = owner_actor(world, customer, "reconcile")
    with Session(world.engine) as db, db.begin():
        graph.reconciliation.reconcile_activity(db, actor, observation)
    return entry


def timer_then_read(delay_seconds, flows=FLOWS, *, timeout_seconds=None):
    """`activity_read` preceded by a timer: the read is dispatched only after it fires.

    With `timeout_seconds`, the read is `patient_read()`'s, for a read that must complete.
    """
    if timeout_seconds is None:
        package, suffix = copy.deepcopy(flow("activity_read", flows)), ""
    else:
        package, suffix = patient_read(timeout_seconds, flows), f"-{timeout_seconds}"
    timer = next(s for s in flow("waits_fast", flows)["body"]["steps"] if s["kind"] == "timer")
    timer = {**copy.deepcopy(timer), "delay_seconds": delay_seconds}
    package["package_id"] = f"timer-then-read-{delay_seconds}{suffix}"
    package["body"]["steps"] = [timer, *package["body"]["steps"]]
    return package


def _read_step(step_id, flows=FLOWS, timeout_seconds=30):
    read = copy.deepcopy(flow("activity_read", flows)["body"]["steps"][0])
    read["id"] = step_id
    read["retry"] = {**read["retry"], "timeout_seconds": timeout_seconds}
    return read


def patient_read(timeout_seconds=30, flows=FLOWS):
    """`activity_read` with a longer step deadline, for a read that must complete.

    The fixture's 2 s deadline is also the read's lease: on a loaded host, claim, read
    and report can outlast it, and the attempt is then uncertain, as designed.
    """
    package = copy.deepcopy(flow("activity_read", flows))
    package["package_id"] = f"activity-read-{timeout_seconds}"
    step = package["body"]["steps"][0]
    package["body"]["steps"][0] = _read_step(step["id"], flows, timeout_seconds)
    return package


def patient_agent(timeout_seconds=240, flows=FLOWS):
    """`agent_tools` and its model document with a longer agent deadline and budget.

    The agent's 30 s bounds the active time of its root and every child together, each
    charged at least its reservation, so the fixture leaves about 7 s per attempt. The
    root's lease never outlives its reservation, and its children run inside it: on a
    slow host they can outlast 7 s, and the root is then uncertain, as designed.
    Returns the package and the model document its runtimes must install.
    """
    from lotuspond_sdk.executable.catalog import catalog_document_digest

    model = json.loads((flows / "agent_tools.model.json").read_text())
    model["name"] = f"model-tools-fixture-{timeout_seconds}"
    model["definition"]["limits"]["timeout_seconds"] = timeout_seconds
    package = copy.deepcopy(flow("agent_tools", flows))
    package["package_id"] = f"agent-tools-{timeout_seconds}"
    package["limits"]["active_seconds"] = timeout_seconds
    package["models"][0]["ref"].update(name=model["name"], sha256=catalog_document_digest(model))
    think = package["body"]["steps"][0]
    think["limits"]["timeout_seconds"] = timeout_seconds
    think["retry"]["timeout_seconds"] = timeout_seconds
    return package, model


def read_iteration(items, concurrency, flows=FLOWS):
    """Iteration over integer items: each item reads once, then yields itself (+0)."""
    package = copy.deepcopy(flow("wide", flows))
    activity = flow("activity_read", flows)
    package["package_id"] = f"read-iteration-{items}-{concurrency}"
    package["connectors"] = copy.deepcopy(activity["connectors"])
    each = package["body"]["steps"][0]
    each["concurrency"], each["max_items"] = concurrency, items
    keep = copy.deepcopy(each["body"]["steps"][0])
    keep["id"] = "keep"
    keep["mapping"]["args"][1]["value"] = 0
    each["body"]["steps"] = [_read_step("read"), keep]
    each["body"]["result"] = {
        "kind": "ref",
        "pointer": "",
        "source": {"id": "keep", "kind": "step"},
    }
    package["input_schema"] = {
        "items": {"kind": "integer", "max": 100000, "min": 0},
        "kind": "array",
        "max_items": items,
    }
    package["body"]["result_schema"]["schema"]["max_items"] = items
    package["output_schema"]["max_items"] = items
    package["limits"] = {
        **package["limits"],
        "max_steps": 10000,
        "active_seconds": 86400,
        "elapsed_seconds": 7200,  # about one item a second (validation finding 17)
        "max_input_tokens": 1000000,
        "max_output_tokens": 1000000,
        "max_cost_microusd": 1000000,
    }
    return package


def parallel_failure_after_success(flows=FLOWS, *, fail_after=4, hold=60, slow=None):
    """Branches: a read that succeeds, a failure after `fail_after` seconds, and a late
    read behind a `hold`-second timer; with `slow` seconds, a second read after a timer."""
    package = copy.deepcopy(flow("parallel_failure", flows))
    activity = flow("activity_read", flows)
    timer = next(s for s in flow("waits_fast", flows)["body"]["steps"] if s["kind"] == "timer")
    package["package_id"] = "parallel-failure-after-success"
    package["connectors"] = copy.deepcopy(activity["connectors"])
    package["models"] = []
    both = package["body"]["steps"][0]
    bad = next(branch for branch in both["branches"] if branch["id"] == "bad")
    bad["body"]["steps"] = [
        {**copy.deepcopy(timer), "id": "first", "delay_seconds": fail_after},
        *bad["body"]["steps"],
    ]
    null = {"kind": "data", "schema": {"kind": "null"}}
    both["branches"] = [
        {
            "id": "fast",
            "body": {
                "steps": [_read_step("read")],
                "result": {"kind": "ref", "pointer": "", "source": {"id": "read", "kind": "step"}},
                "result_schema": null,
            },
        },
        bad,
        {
            "id": "late",
            "body": {
                "steps": [
                    {**copy.deepcopy(timer), "id": "hold", "delay_seconds": hold},
                    _read_step("later"),
                ],
                "result": {"kind": "ref", "pointer": "", "source": {"id": "later", "kind": "step"}},
                "result_schema": null,
            },
        },
    ]
    if slow is not None:
        both["branches"].insert(
            1,
            {
                "id": "slow",
                "body": {
                    "steps": [
                        {**copy.deepcopy(timer), "id": "wait", "delay_seconds": slow},
                        _read_step("held", timeout_seconds=60),
                    ],
                    "result": {
                        "kind": "ref",
                        "pointer": "",
                        "source": {"id": "held", "kind": "step"},
                    },
                    "result_schema": null,
                },
            },
        )
    return package


def strict_child(active_seconds, flows=FLOWS):
    """A subworkflow child that reads once, with its own (possibly stricter) limits."""
    child = copy.deepcopy(flow("subworkflow.child", flows))
    activity = flow("activity_read", flows)
    child["connectors"] = copy.deepcopy(activity["connectors"])
    child["package_id"] = f"strict-child-{active_seconds}"
    child["body"]["steps"] = [_read_step("read")]
    child["body"]["result"] = {
        "kind": "ref",
        "pointer": "",
        "source": {"id": "read", "kind": "step"},
    }
    child["body"]["result_schema"] = {"kind": "data", "schema": {"kind": "null"}}
    child["input_schema"] = child["output_schema"] = {"kind": "null"}
    child["limits"] = {**child["limits"], "active_seconds": active_seconds}
    return child


def parent_of(child, flows=FLOWS):
    """A parent that reads once, then invokes `child` as a subworkflow."""
    from lotuspond_sdk.executable import canonical_executable_json

    parent = copy.deepcopy(flow("subworkflow", flows))
    activity = flow("activity_read", flows)
    parent["package_id"] = "parent-of-" + child["package_id"]
    parent["connectors"] = copy.deepcopy(activity["connectors"])
    parent["subworkflows"] = [
        {
            "id": "child",
            "ref": {
                "kind": "workflow",
                "name": child["package_id"],
                "version": child["version"],
                "sha256": __import__("hashlib")
                .sha256(canonical_executable_json(child))
                .hexdigest(),
            },
        }
    ]
    invoke = parent["body"]["steps"][0]
    invoke["input"] = {"kind": "literal", "schema": {"kind": "null"}, "value": None}
    parent["body"]["steps"] = [_read_step("first"), invoke]
    parent["body"]["result_schema"] = {"kind": "data", "schema": {"kind": "null"}}
    parent["input_schema"] = parent["output_schema"] = {"kind": "null"}
    return parent


def string_signal_package(flows=FLOWS):
    """One external wait whose signal is a string, returned as the result; string input."""
    package = external_waits_package([300], flows)
    package["package_id"] = "string-signal"
    step = package["body"]["steps"][0]
    step["signal_schema"] = {"kind": "string", "max_length": 200}
    package["body"]["result_schema"] = {
        "kind": "data",
        "schema": {"kind": "string", "max_length": 200},
    }
    package["output_schema"] = {"kind": "string", "max_length": 200}
    package["input_schema"] = {"kind": "string", "max_length": 200}
    return package


def two_reads(flows=FLOWS):
    """`activity_slow`'s read, then a second read: a next step that must not run."""
    package = copy.deepcopy(flow("activity_slow", flows))
    package["package_id"] = "two-reads"
    first = package["body"]["steps"][0]
    second = copy.deepcopy(first)
    second["id"] = "next"
    package["body"]["steps"] = [first, second]
    package["body"]["result"] = {
        "kind": "ref",
        "pointer": "",
        "source": {"id": "next", "kind": "step"},
    }
    return package


def private_agent(instructions, content, flows=FLOWS):
    """`agent_tools` with these instructions and one pinned knowledge source, `facts`.

    Returns the package and its knowledge document, which pins the exact `content`
    the source will hold and a citation with one string field, `source`.
    """
    from hashlib import sha256

    from lotuspond_sdk.executable.catalog import catalog_document_digest

    from lotuspond.execution.contracts import canonical_value

    citation_schema = {
        "kind": "object",
        "fields": [
            {"name": "source", "required": True, "schema": {"kind": "string", "max_length": 200}}
        ],
    }
    knowledge = {
        "format_version": 1,
        "kind": "knowledge",
        "name": "knowledge-private",
        "version": "1.0.0",
        "schemas": [],
        "definition": {
            "source_revision": "1",
            "content_sha256": sha256(canonical_value(content)).hexdigest(),
            "freshness_seconds": 3600,
            "classification": "synthetic",
            "citation_schema": citation_schema,
        },
    }
    package = copy.deepcopy(flow("agent_tools", flows))
    package["package_id"] = "private-agent"
    package["knowledge"] = [
        {
            "id": "facts",
            "ref": {
                "kind": "knowledge",
                "name": knowledge["name"],
                "version": knowledge["version"],
                "sha256": catalog_document_digest(knowledge),
            },
            "required_freshness_seconds": 3600,
            "allowed_classifications": ["synthetic"],
            "citation_schema": citation_schema,
        }
    ]
    think = package["body"]["steps"][0]
    think["instructions"] = instructions
    think["knowledge_refs"] = ["facts"]
    return package, knowledge


def serving(world, runtime, seconds=60):
    """Wait until this runtime holds an active session: it is up and polling."""
    from processes import wait_for
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from lotuspond.models import RuntimeSession

    def active():
        with Session(world.engine) as db:
            return db.scalar(
                select(RuntimeSession.id).where(
                    RuntimeSession.instance_id == runtime["instance_id"],
                    RuntimeSession.status == "active",
                )
            )

    return wait_for(active, seconds, interval=0.5)


def form_then_read(timeout_seconds, flows=FLOWS):
    """A human form, then a read that runs only if the form is approved in time."""
    package = form_package(flows, timeout_seconds=timeout_seconds)
    activity = flow("activity_read", flows)
    package["package_id"] = f"form-then-read-{timeout_seconds}"
    package["connectors"] = copy.deepcopy(activity["connectors"])
    package["body"]["steps"].append(_read_step("after"))
    package["body"]["result"] = {
        "kind": "ref",
        "pointer": "",
        "source": {"id": "after", "kind": "step"},
    }
    package["body"]["result_schema"] = {"kind": "data", "schema": {"kind": "null"}}
    package["output_schema"] = {"kind": "null"}
    return package
