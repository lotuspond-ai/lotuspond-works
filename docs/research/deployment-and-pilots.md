# Deployment, documentation, and pilot research

**Research date:** 2026-09-21

**Decision status:** local implementation choice; no hosted deployment or customer validation

## Decisions

Lotuspond uses one Python 3.14 API image that also serves the React/Vite static build, PostgreSQL, Redis, and a small Go collector. Docker Compose is the portable single-host baseline for connected and on-premises deployments. An air-gap script exports the same reviewed images plus configuration and SHA-256 transfer checks. These files do not provision cloud resources.

Documentation uses MkDocs 1.6.1 with its built-in theme. Sources remain Markdown in Git, the build emits static files, and the same result can be published through GitHub Pages or served inside a private network. Docusaurus 3.10.2 is also portable but adds a second React/Node application and dependency tree without a current requirement for interactive docs. A managed documentation product could reduce publishing work, but it would add a vendor runtime and complicate fully offline delivery. MkDocs is the smaller present fit.

The pilot offer is a four-week evaluation and shadow-monitoring engagement for one agent workflow. It measures evidence coverage, critical failures, reviewer disagreement, telemetry loss, operator time, and decision time against a customer baseline. It has explicit safety, confidentiality, and evidence stop conditions. It does not promise compliance, savings, autonomous governance, or universal integration.

## Current authoritative contracts

- OTLP/HTTP JSON uses `Content-Type: application/json`; default signal endpoints include `/v1/traces`, `/v1/metrics`, and `/v1/logs`. The implemented collector supports JSON traces at `/v1/traces` only and forwards them to the Lotuspond-specific `/api/v1/telemetry/otlp` route. This is a bounded gateway, not a general OpenTelemetry Collector distribution. [OpenTelemetry OTLP specification](https://opentelemetry.io/docs/specs/otlp/)
- Go 1.27.1 is the current patched Go line recorded on the official release page. The collector uses only the standard library and pins the build image to 1.27.1. [Go release history](https://go.dev/doc/devel/release)
- Python 3.14.7 is an official maintenance release dated 2026-08-05. The API container pins that version while the package contract remains Python 3.13 or newer. [Python 3.14.7 release](https://www.python.org/downloads/release/python-3147/)
- The evidence CLI pins current Rust 1.98.1. The exact project toolchain plus `Cargo.lock` makes local and CI checks use the reviewed compiler and dependency graph. [Rust release index](https://blog.rust-lang.org/releases/)
- The Rust CLI pins `sha2` 0.11.0, `serde` 1.0.229, and `serde_json` 1.0.151 in `Cargo.toml`; `Cargo.lock` records the resolved transitive set. The RustCrypto implementation provides SHA-256, but a file hash is an integrity primitive rather than a signature or provenance service. [RustCrypto sha2 documentation](https://docs.rs/sha2/latest/sha2/)
- The container inputs are exact tags for repeatable review: Python 3.14.7 slim, Node 24.21.0 Alpine, PostgreSQL 18.6 Alpine, Redis 7.2.16 Alpine, and Go 1.27.1 Alpine. Release automation should additionally resolve and approve image digests and scan results; an exact tag is not immutable supply-chain evidence. [PostgreSQL official image](https://hub.docker.com/_/postgres), [Redis official image](https://hub.docker.com/_/redis), [Node release status](https://nodejs.org/en/about/previous-releases), [Python official image tags](https://hub.docker.com/_/python/tags?name=3&page=1)

## Documentation platform comparison

| Option | Git and hosting model | Offline/on-premises fit | Current decision |
| --- | --- | --- | --- |
| MkDocs 1.6.1 | Markdown plus one YAML file; emits static HTML; supports GitHub Pages and any static host | Strong with built-in assets; small Python dependency set | Selected and pinned |
| Docusaurus 3.10.2 | React/Node site; emits static build; GitHub Pages supported | Strong after dependencies are mirrored; larger application surface | Revisit if versioned interactive docs justify it |
| Managed docs platform | Git sync and hosted presentation vary by vendor | Requires a separate export/self-host contract and vendor review | Deferred; no present need |

MkDocs warns that `gh-deploy` publishes without a review step and can include untracked files. The authored opt-in Pages workflow builds a reviewable artifact and uses the official Pages deployment actions. It requires a configured repository, Pages environment and `LOTUSPOND_DOCS_PUBLISH=true` repository variable; no remote or hosted publication exists yet. [MkDocs deployment guide](https://www.mkdocs.org/user-guide/deploying-your-docs/), [GitHub Pages custom workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages), [Docusaurus deployment guide](https://docusaurus.io/docs/deployment)

## Deployment findings

Docker documents Compose as usable from development through production and recommends production-specific configuration such as immutable application code, adjusted bindings, restart policy, and supporting operational services. The Lotuspond files use read-only application containers, dropped capabilities, loopback host bindings by default, required production configuration, and separate data volumes. This remains a single-host design; high availability and disaster recovery need a separate reviewed plan and exercises. [Docker Compose production guide](https://docs.docker.com/compose/how-tos/production/)

Docker's supported offline transfer primitive is `docker image save` followed by `docker image load`. The bundle adds an external archive SHA-256 and an internal file manifest. The checksum must travel through an independent trusted channel to detect substitution; these scripts provide no signing or publisher identity. [Docker image save reference](https://docs.docker.com/reference/cli/docker/image/save/)

Air-gap acceptance requires more than setting an environment variable: enforce egress denial at the network boundary; mirror and scan approved images; inject secrets locally; provide internal TLS and DNS; validate time synchronization; test backup and restore; and run customer-specific evaluation and telemetry-loss checks. External model, email, identity, and hosted integration adapters must fail closed or be disabled.

The Gym Anything worker is a separate trust boundary. It pins upstream commit `f3dc04649641dba69d5032f7bb6adaa9785c150d` and the upstream constraints checksum, requires an immutable local image id or registry digest, accepts only operator-local allowlists, and invokes the documented CLI using an argv array. The default policy removes egress, capabilities, privilege escalation, image pulls, host devices, and the Docker socket. The present manifest therefore fixes upstream's synthetic `local` runner: it validates episode and verifier plumbing but does not launch the named application. Environments needing KVM, nested containers, external providers, or services require a separate reviewed runner instead of broader shared privileges. [Gym Anything CLI](https://cmu-l3.github.io/gym-anything/docs/reference/cli/), [Gym Anything runners](https://cmu-l3.github.io/gym-anything/docs/core/runners/)

## Pilot evidence model

NIST's AI Risk Management Framework is voluntary guidance for incorporating trustworthiness into design, development, use, and evaluation. Its resource center emphasizes documented, objective, repeatable test, evaluation, verification, and validation processes. The ARIA pilot combined technical/model testing with red-team and field-testing perspectives, which supports separating scenario results from real operating context. Lotuspond's pilot uses these ideas as measurement discipline, not as a certification claim. [NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework), [NIST AI Resource Center](https://airc.nist.gov/), [NIST ARIA evaluation](https://ai-challenges.nist.gov/aria)

The pilot should proceed beyond shadow mode only when the customer has a named production use case, an independent reviewer, acceptable critical-risk findings, sufficient evidence coverage, controlled data handling, measured operator cost, and budget for the next bounded phase. Stop when critical evidence is unavailable, confidential data crosses the agreed boundary, false decisions exceed the agreed tolerance, telemetry loss invalidates conclusions, or the workflow owner cannot supply a stable baseline.

## Known gaps before production claims

- No deployment here was started against a production database or exposed host.
- TLS termination, certificate rotation, DNS, backup storage, restore automation, alert delivery, identity federation, and image scanning are not supplied.
- Collector persistence, protobuf/gzip OTLP, metrics, logs, sampling, and retry storage are not implemented.
- The evidence manifest is unsigned and has no trusted timestamp, identity, transparency log, or secret-store integration.
- Each enabled Gym application, external runner, and voice-provider contract still requires its own reviewed runtime and acceptance test; the generic sandbox policy does not establish compatibility.
- GitHub Pages publication requires a configured repository and explicit publication scope; source presence is not hosted documentation evidence.
