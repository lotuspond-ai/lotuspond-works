import logging
import re
from contextlib import asynccontextmanager
from dataclasses import replace

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .config import Settings
from .database import engine_for, sessions_for
from .fleet.authority import FleetDependencies, UnavailableRecoveryAuthority
from .fleet.catalog import InstalledCatalog
from .fleet.management_routes import FleetRequestBoundary
from .fleet.management_routes import router as fleet_management_router
from .fleet.runtime_routes import router as fleet_runtime_router
from .gateway import router as gateway_router
from .identity import router as identity_router
from .identity_lifecycle import router as identity_lifecycle_router
from .mfa import router as mfa_router
from .models import SchemaVersion
from .monitoring import router as monitoring_router
from .offline_identity import router as offline_identity_router
from .operations import router as operations_router
from .package_contract import MAX_PACKAGE_BYTES
from .packages import router as packages_router
from .schema_version import SCHEMA_VERSION
from .security import RateLimiter
from .workflows import router as workflow_router

logger = logging.getLogger("lotuspond")


class RequestBoundary:
    """Reject oversized bodies before parsing and verify browser mutation origins."""

    def __init__(self, app, settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # HTTP header octets use Latin-1; field-specific validation remains strict.
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope["headers"]
        }

        async def error(status, code, message):
            await JSONResponse({"error": code, "message": message}, status_code=status)(
                scope, receive, send
            )

        package_publication = (
            scope["method"] == "POST"
            and re.fullmatch(r"/api/v1/projects/[^/]+/packages/?", scope["path"]) is not None
        )
        budget = (
            min(self.settings.max_request_bytes, MAX_PACKAGE_BYTES)
            if package_publication
            else self.settings.max_request_bytes
        )
        if package_publication:
            if headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
                await error(415, "unsupported_media_type", "Use an application/json request body.")
                return
            encodings = [
                value.decode("latin-1").lower()
                for key, value in scope["headers"]
                if key.lower() == b"content-encoding"
            ]
            if len(encodings) > 1 or (encodings and encodings[0] != "identity"):
                await error(415, "unsupported_encoding", "Use an uncompressed package body.")
                return
        method = scope["method"]
        if method not in {"GET", "HEAD", "OPTIONS"}:
            # Authorization-bearing API calls still must not smuggle a browser session.
            browser = "cookie" in headers or "origin" in headers
            if browser and headers.get("origin") != self.settings.origin:
                await error(403, "invalid_origin", "The request origin is not permitted.")
                return
            content_type = headers.get("content-type", "").lower()
            # OTLP exporters send binary protobuf by default. Like JSON it is not a
            # CORS-safelisted type, so a cross-site form still cannot submit it, and it is
            # accepted only where trace exports arrive.
            otlp_protobuf = (
                scope["path"] == "/api/v1/telemetry/otlp"
                and content_type.split(";")[0].strip() == "application/x-protobuf"
            )
            if (
                scope["path"].startswith("/api/")
                and "application/json" not in content_type
                and not otlp_protobuf
            ):
                await error(415, "unsupported_media_type", "Use an application/json request body.")
                return
        try:
            length = int(headers.get("content-length", "0"))
        except ValueError:
            await error(400, "invalid_length", "The content length is invalid.")
            return
        if length < 0 or length > budget:
            await error(413, "body_too_large", "The request body exceeds the size limit.")
            return
        chunks = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            total += len(body)
            if total > budget:
                await error(413, "body_too_large", "The request body exceeds the size limit.")
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (
                        b"content-security-policy",
                        b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
                    ),
                ]
                if scope["path"].startswith("/api/"):
                    # One authoritative private policy; strict machine clients
                    # reject duplicated headers even when their values agree.
                    message["headers"] = [
                        (key, value)
                        for key, value in message["headers"]
                        if key.lower() != b"cache-control"
                    ]
                    message["headers"].append((b"cache-control", b"no-store"))
                if self.settings.env == "production":
                    message["headers"].append(
                        (b"strict-transport-security", b"max-age=31536000; includeSubDomains")
                    )
            await send(message)

        await self.app(scope, replay, secure_send)


def create_app(
    settings: Settings | None = None,
    *,
    engine=None,
    fleet_dependencies: FleetDependencies | None = None,
    command_dependencies=None,
) -> FastAPI:
    settings = settings or Settings()
    if (
        fleet_dependencies is not None or command_dependencies is not None
    ) and settings.env != "test":
        raise ValueError("Fleet fixture injection requires test mode")
    owned_engine = engine is None
    engine = engine if engine is not None else engine_for(settings)
    limiter = RateLimiter(settings)

    dependencies = replace(
        fleet_dependencies or FleetDependencies(UnavailableRecoveryAuthority(), InstalledCatalog()),
        installation_id=settings.fleet_installation_id,
        limiter=limiter,
    )

    @asynccontextmanager
    async def lifespan(_):
        yield
        dependencies.close()
        if limiter.client:
            limiter.client.close()
        if owned_engine:
            engine.dispose()

    app = FastAPI(
        title="Lotuspond API", version="0.2.0", lifespan=lifespan, docs_url=None, redoc_url=None
    )
    app.state.settings = settings
    app.state.fleet_dependencies = dependencies
    from .fleet.commands import (
        CommandDependencies,
        CommandService,
        DenyBusinessDispatchAuthority,
        DenyPinnedRecordSource,
        UnavailableCommandTrustAuthority,
    )

    if command_dependencies is not None and type(command_dependencies) is not CommandDependencies:
        raise ValueError("Invalid command hooks")
    command_hooks = command_dependencies or CommandDependencies(
        DenyBusinessDispatchAuthority(),
        DenyPinnedRecordSource(),
        UnavailableCommandTrustAuthority(),
    )
    app.state.command_service = CommandService(
        dependencies.recovery,
        command_hooks.dispatch,
        command_hooks.records,
        command_hooks.trust,
        dependencies=dependencies,
    )
    app.state.engine = engine
    app.state.sessions = sessions_for(engine)
    app.state.limiter = limiter
    app.add_middleware(RequestBoundary, settings=settings)
    app.add_middleware(FleetRequestBoundary, settings=settings)

    @app.exception_handler(HTTPException)
    async def http_error(_, exc):
        body = (
            exc.detail
            if isinstance(exc.detail, dict)
            else {"error": "request_failed", "message": str(exc.detail)}
        )
        return JSONResponse(body, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_, exc):
        # Pydantic errors include the submitted value; never echo it (passwords, tokens, prompts).
        fields = [".".join(str(part) for part in error["loc"]) for error in exc.errors()]
        return JSONResponse(
            {
                "error": "invalid_input",
                "message": "Some request fields are invalid.",
                "fields": fields,
            },
            status_code=422,
        )

    @app.exception_handler(IntegrityError)
    async def conflict(_, exc):
        return JSONResponse(
            {
                "error": "conflict",
                "message": "A record already exists or a related record is invalid.",
            },
            status_code=409,
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_, exc):
        logger.error("Database request failed: %s", type(exc).__name__)
        return JSONResponse(
            {"error": "database_unavailable", "message": "The data service is unavailable."},
            status_code=503,
        )

    @app.exception_handler(Exception)
    async def internal_error(_, exc):
        logger.error("Request failed: %s", type(exc).__name__)
        return JSONResponse(
            {"error": "internal_error", "message": "The request failed. Please try again."},
            status_code=500,
        )

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "product": "Lotuspond", "version": "0.2.0"}

    @app.get("/api/v1/ready")
    def ready():
        try:
            with engine.connect() as connection:
                version = connection.scalar(select(SchemaVersion.version))
            if version != SCHEMA_VERSION:
                raise RuntimeError("schema")
            if limiter.client:
                limiter.client.ping()
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ready", "schema_version": version}

    @app.get("/api/v1/changelog")
    def changelog():
        return {
            "product": "Lotuspond",
            "items": [
                {
                    "version": "0.2.0",
                    "id": "studio-canvas",
                    "date": "2026-10-15",
                    "title": "The Studio composes workflow packages",
                    "description": "Developers can compose a workflow package as a graph in the console: drag start, agent, connector, human review and end nodes onto graph paper, move them, connect them by dragging a port or by keyboard, and edit each node, the package and its declared models, connectors and secret names in an inspector. An admission check in the browser mirrors the registry validator as you work and points at the nodes concerned, a passing draft shows its content digest, and publishing stores an immutable version in the project through the registry. Drafts stay in the browser until published. The Studio runs nothing: a published version is admitted content, not a deployment, an approval or permission to execute.",
                },
                {
                    "version": "0.2.0",
                    "id": "fleet-runtime-foundation-qualification",
                    "date": "2026-10-14",
                    "title": "Installable Linux runtime identity foundation",
                    "description": "The separate runtime 0.1.0 supports private enrollment, process leases and two-phase credential recovery. Prepare, activate and cancel share a rotation quota; lost responses do not extend expiry, and an active-successor retry preserves a later pending successor. Installed-artifact API qualification and recovery guidance describe explicit test authorities. Production recovery, dispatch and command trust providers, business execution and UI remain separate work.",
                },
                {
                    "version": "0.2.0",
                    "id": "fleet-command-admission",
                    "date": "2026-10-14",
                    "title": "Signed runtime command admission and replay fences",
                    "description": "The opt-in fleet API adds session-authenticated command polling and one-use admission claims. Signed commands are checked against current recovery, independent command trust, scoped connections and authoritative action records. Production record and dispatch authorities deny by default. Claims grant no business execution permission.",
                },
                {
                    "version": "0.2.0",
                    "id": "fleet-runtime-identity",
                    "date": "2026-10-14",
                    "title": "Revocable runtime enrollment and process sessions",
                    "description": "The opt-in fleet API adds one-use enrollment, scoped runtime inventory and revocation, and expiring process sessions. Current installation recovery authority and pool policy fence machine authentication. Production recovery authority remains unavailable until a qualified provider is integrated; business execution is a later capability.",
                },
                {
                    "version": "0.2.0",
                    "id": "fleet-management",
                    "date": "2026-10-14",
                    "title": "Scoped connection and runtime pool administration",
                    "description": "An opt-in API lets customer owners and administrators manage immutable connection configurations, secret-reference metadata and runtime pool policies. Installed connector metadata and current scoped references bound publication. This foundation grants no business execution authority.",
                },
                {
                    "version": "0.2.0",
                    "id": "executable-v2-local-tooling",
                    "date": "2026-10-14",
                    "title": "Executable definition SDK and local tooling",
                    "description": "The standalone SDK adds explicit v2 commands for bounded typed definitions, exact local-source compilation, JCS content, private differences, detached package signatures, synthetic capability checks and structural package/plan/runtime schemas. Runtime-command signing and pure binding/transition APIs remain local contracts. V1 bytes and registry admission remain unchanged. These outputs grant no authority; engine execution, Studio, provider support, commercial licensing and production qualification remain separate work.",
                },
                {
                    "version": "0.2.0",
                    "id": "standalone-package-sdk",
                    "date": "2026-10-14",
                    "title": "Standalone workflow package SDK",
                    "description": "Developers can install a Python SDK and use bounded local commands to validate, digest, canonicalize and compare workflow-package v1, generate owner-only keys, sign and verify detached Ed25519 signatures against explicit independent trust, and compare exact synthetic capability declarations. The SDK and registry share the existing validator and canonical bytes. Outputs are exclusive; signatures and declarations grant no tenant authority, deployment admission, approval or execution permission.",
                },
                {
                    "version": "0.2.0",
                    "id": "compose-identity-settings",
                    "date": "2026-10-14",
                    "title": "Deployment identity settings",
                    "description": "Compose installations now apply the configured installation-wide authenticator requirement and whole-conversation mail timeout. An enabled authenticator requirement still refuses startup without its encryption key; production operator MFA remains mandatory.",
                },
                {
                    "version": "0.2.0",
                    "id": "workflow-package-registry",
                    "date": "2026-10-13",
                    "title": "Immutable workflow package registry",
                    "description": "Developers can publish validated workflow packages into workspace projects and retrieve exact canonical content, integrity digests and the validator revision used at admission. Contract discovery reports the installation's effective publication byte limit. Versions are immutable, retries are idempotent, and workspace roles and key scopes apply. Publication does not grant trust, release approval or permission to execute.",
                },
                {
                    "version": "0.2.0",
                    "id": "workflow-package-foundation",
                    "date": "2026-10-13",
                    "title": "Workflow package validation",
                    "description": "Developers can validate a versioned workflow package locally against a strict contract and calculate its content digest. The validator checks graph structure, declared references and agent limits without running the workflow. This foundation checks declarations; signatures, deployment and production execution remain planned.",
                },
                {
                    "version": "0.2.0",
                    "id": "collector-spool",
                    "date": "2026-09-23",
                    "title": "A durable collector spool",
                    "description": "The telemetry collector can keep batches on disk while the API is unreachable. With COLLECTOR_SPOOL_DIR set, every accepted batch is written durably before it is forwarded; a batch the API cannot answer is acknowledged with 202 and delivered later, across restarts and crashes, and a batch the API refuses is kept for the operator. A full spool refuses new batches with 503, so exporters keep them and nothing is dropped silently. The spool is off unless configured.",
                },
                {
                    "version": "0.2.0",
                    "id": "signed-evidence-manifests",
                    "date": "2026-09-23",
                    "title": "Signed evidence manifests",
                    "description": "The lotuspond-evidence CLI can now sign an artifact manifest with an Ed25519 publisher key and verify it against a public key you trust, so a manifest shows who published it as well as which files it covers. Keys are standard PEM files and the signed message is documented, so OpenSSL can check a signature without Lotuspond. Private keys are written readable only by their owner and are never overwritten.",
                },
                {
                    "version": "0.2.0",
                    "id": "otlp-protobuf",
                    "date": "2026-09-23",
                    "title": "OTLP protobuf ingestion",
                    "description": "OpenTelemetry exporters can now send traces with their default binary protobuf encoding, to the collector or straight to the API, with or without gzip; no switch to JSON is needed. A protobuf span is stored exactly as the same span sent as JSON, and the exporter receives the standard response, including how many spans were not kept and why. Usage counts such as gen_ai.usage.input_tokens are now kept as evidence instead of being redacted as if they were credentials.",
                },
                {
                    "version": "0.2.0",
                    "id": "accessibility-review",
                    "date": "2026-09-23",
                    "title": "Accessibility review",
                    "description": "The console and public pages were reviewed against WCAG 2.2 AA and corrected. The focus ring and text-field edges now hold 3:1 contrast on every surface, including the pond scene, and filter placeholders are full-contrast text. Search keeps focus inside and announces how many results match, closing a record drawer returns you to the button that opened it, and every row action names its record. Each page titles the browser tab and is announced when you move to it, the narrow-screen menu closes on Escape, Home no longer scrolls sideways on a 320px screen, and the wordmark is legible over the scene again.",
                },
                {
                    "version": "0.2.0",
                    "id": "console-redesign",
                    "date": "2026-09-23",
                    "title": "A redesigned console",
                    "description": "The console now opens on Home, where a six-step checklist built from your workspace's own records leads to a first release decision, each step opening the right form. Navigation is grouped in the order work happens with an icon beside every label, a workspace switcher sits at the top, and Search or ⌘K jumps to any page or create action. Lists have readable columns and relative times, details open in a drawer, and release decisions carry a verdict stamp. The public platform preview renders the same components.",
                },
                {
                    "version": "0.2.0",
                    "id": "public-introduction",
                    "date": "2026-09-22",
                    "title": "A clearer public introduction",
                    "description": "The signed-out page opens on a drawn pond scene, and then shifts to a field-record style: outcomes drawn as traces on graph paper, a workflow thread that inks each step as you scroll, ink glyphs for deployment and a stamped decision record. A labelled sample workspace lets visitors change scenario outcomes and policy limits and watch the release rules decide, and the page lists the latest changelog entries. It explains how a release decision is reached, quoting the exact reasons a policy records, why a missing or unknown outcome blocks a release, what each workflow retains, and where Lotuspond can be installed. Choosing an individual or organization account from the page opens registration with that choice selected.",
                },
                {
                    "version": "0.2.0",
                    "id": "subscription-entitlements",
                    "date": "2026-09-22",
                    "title": "Subscription levels, assisted reset and A2A",
                    "description": "A workspace's plan now decides its monthly trace allowance and retention window, with plan changes reserved to operators and a dry-run-first retention job. Traces export as canonical JSON on every plan. An organization administrator can clear a member's lost authenticator without being able to enter their account, and an A2A adapter evaluates agents over JSON-RPC where only a completed task is judged.",
                },
                {
                    "version": "0.2.0",
                    "id": "compressed-telemetry",
                    "date": "2026-09-21",
                    "title": "Compressed OTLP ingestion",
                    "description": "The collector and the telemetry endpoint accept the gzip that OTLP/HTTP exporters send by default. A gzip body must be a single member, and its decompressed size is bounded by the same budget as the wire body, so a small highly compressible payload is refused rather than expanded.",
                },
                {
                    "version": "0.2.0",
                    "id": "account-change-notifications",
                    "date": "2026-09-21",
                    "title": "Account change notices and installation-wide authenticators",
                    "description": "When a password or authenticator changes, Lotuspond queues a notification to the account mailbox. It states what changed and when, and deliberately carries no link and no code. A disabled mailbox never blocks the security change itself. Operators can also require an authenticator for every workspace on the installation.",
                },
                {
                    "version": "0.2.0",
                    "id": "organization-authenticator-policy",
                    "date": "2026-09-21",
                    "title": "Organization authenticator policy",
                    "description": "An organization administrator who already holds an authenticator can require one for every member and key owner in that organization. Personal workspaces are unaffected, and the SMTP outbox now bounds the whole conversation rather than each socket operation.",
                },
                {
                    "version": "0.2.0",
                    "id": "customer-execution-distribution",
                    "date": "2026-09-21",
                    "title": "Customer-side execution distribution",
                    "description": "HTTP, MCP, voice, and release-gate commands ship in a separate nonroot Node image. Gym tasks stay on a host-side kit with their own Docker image. API URLs accept an origin or the /api/v1 base.",
                },
                {
                    "version": "0.2.0",
                    "id": "supervised-http-runners",
                    "date": "2026-09-21",
                    "title": "Supervised runners and HTTP agent evaluation",
                    "description": "Run an operator-configured HTTP agent with bounded complete evidence. Scenario subprocesses enforce deadlines, keep control-plane credentials separate, and stop when their lease or authorization is lost. Infrastructure failures remain inconclusive.",
                },
                {
                    "version": "0.2.0",
                    "id": "0.2.0",
                    "date": "2026-09-21",
                    "title": "Agent assurance platform",
                    "description": "Individual and organization accounts, session controls, password changes, project-scoped evaluations, live monitors, voice transcripts, semantic assessments, and operator controls on the Python API.",
                },
                {
                    "version": "0.2.0",
                    "id": "identity-lifecycle",
                    "date": "2026-09-21",
                    "title": "Verified identity lifecycle",
                    "description": "Mailbox verification, password recovery, organization invitations, and an encrypted operator-managed mail outbox.",
                },
                {
                    "version": "0.2.0",
                    "id": "actionable-monitoring",
                    "date": "2026-09-21",
                    "title": "Actionable production monitoring",
                    "description": "Failed and unavailable assessments now create distinct incidents with audited resolution history and optional operator-managed signed webhook alerts.",
                },
                {
                    "version": "0.2.0",
                    "id": "offline-enrollment-and-trace-costs",
                    "date": "2026-09-21",
                    "title": "Offline enrollment and trace cost provenance",
                    "description": "Air-gapped installations can provision new operator-attested accounts with single-use enrollment links. Trace costs distinguish reported amounts, unavailable measurements, and preserved values of uncertain provenance.",
                },
                {
                    "version": "0.2.0",
                    "id": "authenticator-app-mfa",
                    "date": "2026-09-21",
                    "title": "Authenticator app multi-factor authentication",
                    "description": "Accounts can protect sign-in with time-based authenticator codes and single-use recovery codes. Production platform operators require a verified identity and a multi-factor session.",
                },
                {
                    "version": "0.2.0",
                    "id": "worker-operations",
                    "date": "2026-09-21",
                    "title": "Worker and queue operations",
                    "description": "Multi-factor-protected operators can inspect aggregate evaluation, mail, and alert queue health plus bounded last-poll observations without exposing customer evidence.",
                },
                {
                    "version": "0.2.0",
                    "id": "bounded-run-snapshots",
                    "date": "2026-09-21",
                    "title": "Bounded runner claims",
                    "description": "Run creation now limits frozen scenario snapshots to 4 MiB, and capability-aware claiming skips incompatible work before taking database locks.",
                },
            ],
        }

    app.include_router(identity_router)
    app.include_router(identity_lifecycle_router)
    app.include_router(offline_identity_router)
    app.include_router(mfa_router)
    app.include_router(operations_router)
    app.include_router(packages_router)
    app.include_router(fleet_management_router)
    app.include_router(fleet_runtime_router)
    app.include_router(workflow_router)
    app.include_router(monitoring_router)
    app.include_router(gateway_router)

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str, request: Request):
        if path.startswith("api/") or path == "api":
            return JSONResponse(
                {"error": "not_found", "message": "API route not found."}, status_code=404
            )
        root = settings.web_dist.resolve()
        asset = (root / path).resolve()
        if asset.is_relative_to(root) and asset.is_file():
            return FileResponse(asset)
        if path.startswith("assets/") or "." in path.rsplit("/", 1)[-1]:
            return JSONResponse(
                {"error": "not_found", "message": "Asset not found."}, status_code=404
            )
        index = root / "index.html"
        if index.is_file():
            return FileResponse(index)
        return JSONResponse(
            {"error": "frontend_unavailable", "message": "Build the Lotuspond web application."},
            status_code=503,
        )

    return app


app = create_app()
