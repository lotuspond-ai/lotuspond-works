# Evaluation runners

A runner executes a frozen scenario set and submits measured evidence for a candidate revision. It runs on an operator-controlled host, separately from the API. Register a runner with the modalities it can execute, create a scoped API key containing `run`, and queue a run in the same workspace. An empty capability list means unrestricted compatibility; use an explicit list for specialized workers.

Configure `LOTUSPOND_API_URL` as either the API origin, such as `https://lotuspond.example.com`, or the full API base ending in `/api/v1`. Both forms resolve to the same `/api/v1` prefix. Resolve `LOTUSPOND_API_KEY` through your protected runtime configuration. Set `LOTUSPOND_RUNNER_ID` to the registered identifier. Then start the selected local adapter:

```bash
npm run runner -- ./protocol-adapters/src/http-runner.mjs
```

The packaged execution image uses the same variables without a repository path:

```bash
docker run --rm --env-file /protected/execution.env IMAGE_ID runner-http
```

The API never accepts an uploaded adapter or scenario-supplied command. Use reviewed adapter files on a POSIX host. A subprocess is a lifecycle boundary, not a security sandbox: run trusted adapters under a restricted operating-system account and the network/filesystem policy appropriate to the workload. Windows process-tree management is not implemented. The HTTP runner in the execution image does not receive a Docker socket.

## HTTP agent endpoints

The HTTP adapter accepts scenario input `{"prompt":"Your test input"}` and one deterministic expectation: `{"contains":"required text"}` or `{"equals":"exact response"}`. A missing or unsupported expectation leaves the verdict unknown. Text, latency and unavailable cost provenance are retained separately from the verdict.

| Setting                             | Purpose                                                                               |
| ----------------------------------- | ------------------------------------------------------------------------------------- |
| `LOTUSPOND_HTTP_ENDPOINT`           | Operator-selected URL; HTTPS, or verified loopback HTTP                               |
| `LOTUSPOND_HTTP_MODE`               | `plain-input` (default) or `openai-chat`                                              |
| `LOTUSPOND_HTTP_TOKEN`              | Optional separate agent bearer credential                                             |
| `LOTUSPOND_HTTP_RESPONSE_PATH`      | Optional dotted JSON path, such as `choices.0.message.content`                        |
| `LOTUSPOND_HTTP_MODEL`              | Optional model identifier for chat requests                                           |
| `LOTUSPOND_HTTP_SYSTEM_PROMPT`      | Optional system message for chat requests                                             |
| `LOTUSPOND_HTTP_TIMEOUT_MS`         | Complete-operation deadline, 250–60,000 ms; default 20,000                            |
| `LOTUSPOND_HTTP_MAX_INPUT_BYTES`    | Encoded request bound; default 65,536, maximum 262,144                                |
| `LOTUSPOND_HTTP_MAX_RESPONSE_BYTES` | Full response bound; default 1,048,576, maximum 2,097,152                             |
| `LOTUSPOND_HTTP_MAX_OUTPUT_BYTES`   | Retained output bound; default 65,536, maximum 262,144                                |

Plain mode sends `{"input":"Your test input"}`. Chat mode sends user `messages`, with configured model/system fields. The adapter extracts a complete string from JSON or `text/plain`; a configured response path takes precedence over the documented standard text fields. It does not send scenario-provided headers or select destinations from input. Keep both the control-plane key and agent credential private and distinct.

Redirects, compressed bodies, invalid JSON/UTF-8, truncated responses, missing output, excessive evidence and expired deadlines produce unknown results. Ambient proxies are ignored; HTTPS certificate verification remains enabled. A completed response that fails its supported assertion produces `passed:false`. A successful HTTP status alone never passes an evaluation. No retries or unmeasured zero-cost claims are added. This text adapter does not transcribe audio.


## Deadlines, credentials and leases

| Setting                                | Behavior                                                                               |
| -------------------------------------- | -------------------------------------------------------------------------------------- |
| `LOTUSPOND_RUNNER_SCENARIO_TIMEOUT_MS` | 1,000–60,000 ms; default 60,000; parent-enforced scenario deadline                     |
| `LOTUSPOND_RUNNER_RENEW_INTERVAL_MS`   | 1,000–60,000 ms; default 60,000; independent lease renewal                             |
| `LOTUSPOND_RUNNER_ONCE`                | `true` performs one claim cycle and exits; an empty queue is permitted                 |
| `LOTUSPOND_RUNNER_ADAPTER_ENV`         | Comma-separated additional environment names explicitly permitted for a custom adapter |

The child receives a small system environment and the reviewed HTTP settings. Other credentials are absent unless explicitly selected as custom adapter configuration. Reserved Lotuspond control-plane and loader variables cannot be opted in. This filters environment inheritance; it does not prevent trusted code from reading files accessible to its operating-system account.

The API grants a 15-minute lease. The runner renews while a scenario executes. If renewal fails, authorization is revoked or the lease is cancelled, it stops active work and discards the unfinished batch. Cancellation is detected at the next renewal request; it is not an immediate remote signal. SIGTERM/SIGINT also abort active work. Cooperative cleanup receives up to five seconds before forced process-group termination; external resources need adapter-specific cleanup. An uncertain teardown stops the runner rather than starting more work.

A fresh process means module-level state does not persist between scenarios. Return an object with `passed` (`true`, `false`, or `null`), object `output`, optional object `evidence`, and optional measured `costUsd`. Adapter exceptions, crashes, timeouts and incomplete evidence stay unknown. Preserve output/artifact provenance when implementing a custom adapter; a fabricated `passed:true` remains an untrusted assertion.

New runs support 1–1,000 scenarios and at most 4 MiB of canonical UTF-8 frozen snapshot data. Scenario IPC is bounded to 1 MiB. The API client allows 2 MiB requests and 8 MiB responses. Split large suites or retain separately governed artifacts when these limits are exceeded; never truncate evidence and treat the remainder as a pass.

## Where the adapter runs

The supervised runner executes your own adapter on your own host under your own account. Its process group bounds how long a scenario may run and guarantees the processes it owns are cleaned up; it is **not** a sandbox and does not isolate the adapter from the machine.

If you run the runner as a long-lived service rather than from your own shell, set `LOTUSPOND_ADAPTER_ROOT` to the directory that may supply adapters. The runner then imports only from inside it, resolving symlinks first so a link inside the root cannot point out of it. Left unset, you name your own adapter file as before.
