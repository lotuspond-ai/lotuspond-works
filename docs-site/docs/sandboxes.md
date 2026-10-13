# Application sandboxes

Lotuspond can launch one operator-reviewed Gym Anything environment and task in a restricted container. The worker accepts identifiers, steps, and a seed. It never accepts a command, image, mount, environment variable, or network setting from an API request.

## Trust boundaries

An operator manifest pins the upstream Gym Anything commit, dependency constraints checksum, immutable runtime image, allowlisted environments and tasks, and maximum resource limits. The manifest lives in a protected local directory and is not uploaded through the product API.

Each run also needs a five-minute HMAC grant bound to the manifest id, environment, task, run id, steps, seed, expiry, and nonce. The backend must authenticate the human or API key, verify workspace access and scope, and then ask the trusted worker to issue or consume a grant. A valid grant does not grant access to another workspace or task.

## Isolation policy

The current launcher uses Docker with:

- no network and no image pull;
- read-only root filesystem and a bounded temporary filesystem;
- all Linux capabilities dropped and `no-new-privileges` enabled;
- the worker's non-root uid and gid;
- CPU, memory, process-count, and wall-time limits;
- one writable artifacts directory;
- no Docker socket, host devices, privileged mode, extra mounts, or workload secrets.

These restrictions deliberately make some Gym Anything environments incompatible. Environments that require KVM, Docker-in-Docker, online model providers, a desktop device, or external services need a separate threat model and reviewed runner. Do not weaken a shared manifest to make an environment start.

The baseline manifest fixes Gym Anything's synthetic `local` runner. It exercises task loading, episode artifacts, and verifier execution with blank observations and no-op actions; it does not start the named application. `sandbox/real-app/` provides a separately reviewed, operator-only real terminal smoke. Its trusted host controller uses Docker, while the application container receives no Docker socket, device, privilege, mount, secret, or network. The smoke fixes the task, action, image, and policy and is not exposed through the API.

## Operator workflow

Build the runtime from the exact reviewed upstream checkout with `sandbox/build-runtime-image.sh`, scan it, record the resulting image id or registry digest, and place a reduced allowlist in the manifest directory. Run `lotuspond-sandbox doctor` to check Docker availability without launching a workload. Follow the full local procedure in `sandbox/README.md`.

The local validation on 2026-09-21 ran the full grant and launcher path for `moodle_env@0.1` / `enroll_student@1`. The isolated process exited successfully and retained 1,205 bytes of artifacts, while the independent Gym verifier returned `passed: false`, score 0, because the synthetic runner produced no application result. This validates plumbing and the separation between process outcome and task decision; it is not Moodle application evidence.

The separate real-app validation ran pinned upstream task `example.basic_x11@0.1` / `hello@1` against a genuine Xvfb/Fluxbox/xterm application image. It used XTEST keyboard input, produced the exact terminal transcript, observed the expected X11 window, captured the screen, and received a score of 100 from the byte-pinned upstream verifier. Docker inspection confirmed the configured isolation controls and cleanup was confirmed. This is evidence for one local terminal task only; it is not coverage for Moodle or the wider Gym catalog.

Follow the build, immutable image review, execution, artifact, and cleanup procedure in `sandbox/README.md`. Keep the trusted controller operator-only. Adding another application requires a new image, fixed task and verifier provenance, independent application-state checks, and a separate threat review.

The run writes a plan, Gym Anything artifacts, and a generic receipt. Process output is discarded because it may contain prompts or credentials. Protect, redact, and expire the artifact directory according to the project policy.

## What a receipt proves

A successful receipt shows that the pinned container command exited successfully under the recorded resource policy. The Gym summary contains the independent task pass/fail decision. Neither proves the upstream verifier is correct, the application behaved safely, the image passed a vulnerability review, network isolation exists outside Docker, or the result is suitable for release. Review the retained task artifacts and verifier provenance before using the outcome in a decision.

## Agent-driven execution

The separate real xterm controller now supports an operator-managed agent at an explicit loopback HTTP endpoint. Every request contains the pinned task and a bounded XWD screen observation with hashes; the response contains one allowlisted `type_text`, `key`, or `finish` action. Actions run only inside the isolated application container. Six steps, a 45-second application deadline, 4 KiB action responses, and a 20 MiB artifact limit bound each run. HTTP attempts have a hard deadline and ignore ambient proxies. The Node adapter adds its own watchdog and reports infrastructure or cleanup failures as unknown.

Use `sandbox/leased-run-adapter.mjs` with the normal Lotuspond runner. The reviewed scenario selects only `example.basic_x11@0.1` and `hello@1`. Destination, image, controller, task/verifier hashes, runtime policy, and output location are operator configuration, never scenario-supplied commands. The API key is kept out of the controller and application environments. Successful results require the upstream verifier plus independent terminal/window/screen evidence and confirmed cleanup. Cost remains unavailable unless separately measured.

A local schema-v6 run on 2026-09-21 completed through the actual claim/evaluate/submit API path with a deterministic test adapter. The persisted result was passed with unavailable cost, and its receipt hash matched the retained artifacts. This validates the real application and product integration boundary; it does not establish model performance or broader application coverage. Full commands, protocol limits, and the deterministic fixture are in `sandbox/README.md`.

The customer execution archive copies this host-side kit (`leased-run-adapter.mjs`, the standard-library controller, task, verifier, license and provenance) together with the reviewed application image. HTTP, MCP and voice commands stay in a Node image that does not receive the Docker socket. Operators still need host Python and Docker for the xterm task, and host Node only if they run the leased adapter against the API. See [deployment](deployment.md) and `deploy/execution/INSTALL.md`.
