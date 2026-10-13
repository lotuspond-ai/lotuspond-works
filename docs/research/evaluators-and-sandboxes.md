# Evaluators and software sandboxes

Verified 2026-09-21. This note records the contracts used by the Python evaluation adapters. It does not claim a configured provider, a running sandbox, or production validation.

## TypeSafe Jev

TypeSafe's documentation index is the source-of-truth entry point: <https://docs.typesafe.ai/llms.txt>. The official HTTP reference documents `POST https://api.typesafe.ai/v1/systemone` with Bearer authentication and a body containing `state`, `model`, and a named `questions` map: <https://docs.typesafe.ai/api.md>. Jev supports three typed question forms: `noul`, `choice`, and `score`. The adapter uses a single `noul` question so Lotuspond can map a probability to explicit pass, fail, and review bands in code.

The official Python SDK is `typesafe-sdk`: <https://docs.typesafe.ai/sdk/python.md> and <https://github.com/typesafe-ai/typesafe-sdk-python>. The verified local dependency is version `0.7.0`. Its asynchronous contract is `AsyncTypeSafeClient(api_key=..., base_url=..., model=..., timeout=...)` followed by `await client.system_one(state=..., questions=...)`: <https://docs.typesafe.ai/sdk/python/api/clients/async.md>. The SDK documents typed timeout, connection, API, and response-validation failures: <https://docs.typesafe.ai/sdk/python/api/exceptions.md>.

Lotuspond's `JevEvaluator` requires an operator-supplied HTTPS base URL and API key. An absent URL or key returns `unknown` without constructing an SDK client. URLs with credentials, queries, fragments, or a non-HTTPS scheme are rejected. The default model alias is `jev-latest`; the provider's resolved model value is retained separately as the result version. A Noul probability at or above `0.8` passes, at or below `0.2` fails, and the interval between them remains `unknown`. These thresholds are configuration, not model guarantees. Provider failures, timeouts, malformed responses, incomplete voice transcripts, and oversized state all return `unknown` and never pass.

The adapter sends the supplied criteria and the complete JSON input, context, expected value, output, modality, and finalized transcript. It does not send credentials in state, log request bodies, or expose provider error bodies. A live call was intentionally not made because no operator configuration was supplied.

## Transcript and live assessment contract

Transcript segments have stable ids, `caller | agent | system` speakers, text, optional millisecond start/end timestamps, an optional non-negative sequence number, and an `is_final` flag. A partial segment can be revised and then replaced by its final version. A final segment is immutable; conflicting content is rejected. Final segments are ordered by complete sequence metadata when available, then by timestamps, then by arrival order with an explicit provenance warning.

The live assessment adapter returns `unknown` while any segment is partial or when no final segment exists. Once the transcript is final, it sends the full normalized transcript and the same criteria to the configured evaluator. This keeps interim speech-recognition output from becoming release evidence.

## Deterministic evaluation

Deterministic evaluation is code-owned. Arbitrary criteria text does not pass by itself. The simple authoring contract is:

```json
{
  "input": { "prompt": "Summarize release rel-42" },
  "expected": { "contains": "rel-42" },
  "output": "Release rel-42 is ready for review."
}
```

`expected.contains` performs a case-sensitive substring check against a string output or canonical JSON. `expected.equals` performs JSON equality. Advanced callers may put `expected_output`, `required_output_fields`, and `forbidden_output_fields` under `context.deterministic_assertions`. Unsupported or malformed assertions return `unknown`; a configured mismatch returns `failed`; all configured assertions must pass before the result is `passed`.

## Gym-Anything

The authoritative upstream repository is <https://github.com/cmu-l3/gym-anything>. Its official documentation describes three independently replaceable parts: Core, benchmarks, and agents. Core loads an environment, captures observations, accepts actions, runs verification, saves artifacts, and closes the run: <https://cmu-l3.github.io/gym-anything/docs/core/>. The benchmark catalog lives under `benchmarks/cua_world/environments`; each environment uses `env.json`, and task folders commonly contain `task.json`, setup/export hooks, and a verifier: <https://cmu-l3.github.io/gym-anything/docs/benchmarks/> and <https://cmu-l3.github.io/gym-anything/docs/benchmarks/tasks-verifiers/>.

The upstream `main` commit verified with `git ls-remote` on 2026-09-21 was `f3dc04649641dba69d5032f7bb6adaa9785c150d`. That observation can drift; a production catalog must pin a reviewed full commit SHA instead of a branch.

Lotuspond currently implements a bounded catalog parser and launch specification, not an executor. A catalog must use schema `lotuspond.gym-anything.catalog.v1`, the exact authoritative repository URL, a full lowercase commit SHA, normalized paths under `benchmarks/cua_world/environments/`, and allowlisted environment/task identifiers. The launch specification produces an argv tuple such as `("gym-anything", "run", "moodle", "--task", "enroll_student")`; it never invokes a shell, downloads a repository, runs setup hooks, opens VNC, or starts a container.

Before enabling execution, the owning runtime still needs a reviewed checkout/download process with digest verification, isolation and network policy, resource/time quotas, lifecycle cleanup, artifact redaction and retention, audit scope, and verifier trust rules. The pinned catalog must also be reviewed against the selected upstream commit because Lotuspond does not infer environment/task safety from valid JSON.

## Archive recovery assessment

The historical service was inspected read-only without environment files, secret stores, database calls, or external runtime calls. Useful recovery candidates were:

- `packages/g2-server/src/agent-gym/voice-call.evaluation.ts`: explicit metric coverage, missing-evidence attribution, latency/tool/guardrail gates, and bounded structural evidence.
- `packages/g2-server/src/agent-gym/voice-call.provider-normalization.ts`: bounded transcript extraction, speaker normalization, and timestamp normalization across provider envelopes.
- `packages/shared/src/gym-runtime/types.ts` and `client.ts`: a separate runtime boundary, bounded timeouts, manual redirect handling, task definitions, and structured attempt results.
- `packages/agent-server/src/sandbox/sandbox.service.ts`: normalized relative paths, identifier bounds, byte limits, and configured-only runtime creation.
- `packages/g2-server/src/a2a/evaluation.service.ts`: measured latency and structured completion/failure evidence around an external peer call.

The archived database coupling, provider-specific webhooks, arbitrary shell-command surface, legacy configuration names, and product branding were not restored. The Python implementation retains the safety ideas while using new Lotuspond models and official current provider contracts.

## Local verification

`backend/.venv/bin/python -m pytest backend/tests/test_evaluation*.py -q` passed 24 tests plus seven subtests on Python 3.14. The tests cover deterministic pass/fail/unknown behavior, the `expected.contains` authoring contract, transcript ordering and immutable finals, partial-versus-final live assessment, Jev pass/fail/review bands, unavailable and timeout outcomes, full evidence submission, HTTPS configuration validation, and strict Gym-Anything catalog/launch validation. One hermetic test uses the installed official TypeSafe SDK with an `httpx2.MockTransport` and verifies the `/v1/systemone` path and Bearer header without making a network call.

No live Jev request, Gym-Anything installation, repository download, software environment execution, voice provider connection, database operation, AWS change, deployment, or hosted-behavior check was performed.

## Customer HTTP adapter recovery, 2026-09-21

The separately archived `http-endpoint-agent.service.ts` (SHA-256 `c22bb3b27587eac195968dc8995703b6907b898cd5ef8e011d573a2f93c18379`) contains real plain-input/chat request mapping and response-path extraction used by its scenario executor. Those behaviors are adapted in `protocol-adapters/src/http-runner.mjs`. Its previous timeout ended at response headers and its truncated/infrastructure outcomes could be treated as task failures; those behaviors were replaced with a complete-operation deadline, bounded complete evidence and explicit unknown verdicts. No archived environment configuration, credentials, history or branding were copied.

Further inspected recovery candidates are run diagnostics/artifact navigation, strict scenario-pack import and A2A transport. They are not reported as restored. The earlier benchmark catalogs were metadata, not proof of working benchmark harnesses, and were not promoted to validated product capabilities.
