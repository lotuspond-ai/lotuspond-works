# Lotuspond Gym Anything sandbox launcher

This worker launches one allowlisted Gym Anything task pipeline in a digest-pinned OCI image. It uses the upstream `gym-anything run <environment> --task <task>` contract and records a plan plus a generic receipt. It never invokes a shell, accepts a command from an API request, mounts the Docker socket, passes credentials into the workload, enables VNC, or allows network egress.

The baseline reviewed manifest fixes `runner` to upstream's `local` runner. Upstream defines that runner as synthetic observations with no-op actions and reset commands. It validates environment discovery, episode lifecycle, artifact generation, and verifier execution, but it does not launch or exercise the named application. A successful baseline launcher receipt means the Gym process completed; the Gym summary artifact carries the separate task decision.

`real-app/` is a separate operator-only smoke test for one genuine X11 application. A trusted host controller starts an immutable Xvfb, Fluxbox, and xterm image, focuses the real terminal, and injects the reviewed Gym keyboard action with XTEST. The application container receives no Docker socket, host device, mount, secret, or network. The controller accepts no task, command, action, image, or runtime-policy argument; those values are fixed by its reviewed bundle. It is deliberately not connected to the product API.

The reviewed upstream input is Gym Anything commit `f3dc04649641dba69d5032f7bb6adaa9785c150d` and its `constraints-ci.txt` SHA-256 `0818490024cacfbaa221f21c428bd06a14b1045e2f70f647a3a01f0cbdf54f79`. Updating either requires a source, dependency, environment, task, and image review.

## Build the runtime in a connected environment

Clone Gym Anything separately, check out the exact commit, and leave that checkout clean. Then run:

```bash
sandbox/build-runtime-image.sh /absolute/path/to/gym-anything lotuspond-gym-anything:reviewed
```

The script checks the Git revision and constraints hash, creates a build context from `git archive` so Git metadata and local files are excluded, and prints the immutable local image id. Put that `sha256:...` value into an operator manifest. For a registry flow, push the reviewed image and use its `registry/name@sha256:...` digest instead. Export the final image with `docker image save` for an air-gapped installation; do not re-resolve Python packages inside the isolated network.

The upstream constraints pin versions but do not provide package-file hashes. Treat the built image digest, software-bill-of-materials output, vulnerability scan, and registry policy as the reviewed supply-chain unit. Rebuilding later can retrieve different artifacts under the same package versions; it must produce a new image review even when the source commit is unchanged.

The base image installs the upstream `benchmark` dependency group. Individual applications can require host devices, KVM, emulators, services, or additional packages. Review and build a dedicated image and manifest for each enabled environment. The launcher intentionally does not grant privileged mode, devices, the Docker socket, or egress, so real application runners are not enabled by this baseline.

## Reviewed real terminal smoke

The separate real-app bundle vendors the exact upstream `docker_env/tasks/hello` task and verifier bytes from commit `f3dc04649641dba69d5032f7bb6adaa9785c150d`. Their SHA-256 values are hardcoded in the trusted controller as well as recorded in the bundle manifest, so changing both a file and its manifest hash still fails closed. The application Dockerfile pins Ubuntu 24.04 by digest and pins its direct X11 package versions. Its final immutable image id is the runtime review unit because transitive Ubuntu packages are not locked by artifact hash.

Build in a connected review environment, inspect and scan the result, and place its exact `sha256:...` image id in `real-app/manifest.json`. Task execution itself is offline:

```bash
docker build --pull=false --tag lotuspond-gym-real-app:reviewed sandbox/real-app
docker image inspect lotuspond-gym-real-app:reviewed --format '{{.Id}}'
mkdir -p /absolute/protected/real-app-runs
PYTHONPATH=sandbox/src python3.13 -m lotuspond_sandbox.real_app \
  --bundle sandbox/real-app --output-dir /absolute/protected/real-app-runs
```

The controller inspects effective Docker configuration before acting. It requires a read-only root, no network, no capabilities, `no-new-privileges`, UID/GID 10001, private IPC, no devices, no bind mounts, and fixed CPU, memory, PID, tmpfs, and wall-time bounds. It writes the terminal transcript, X11 window tree, fixed-resolution XWD capture, Gym trajectory, upstream verifier result, and a receipt. A pass requires the exact transcript from the application, the expected real window, a bounded screen capture, and the pinned upstream verifier. Every attempted launch is reconciled by its random name and run label; cleanup must be confirmed even if `docker run` times out or returns an error.

## Operator-managed agent loop

`lotuspond_sandbox.agent_loop` runs the same pinned xterm task as an observation/action loop. The only supported agent destination is an explicit numeric loopback URL such as `http://127.0.0.1:8765/v1/action`; credentials, redirects, query parameters, HTTPS, hostnames, and remote addresses are rejected. Each request contains the fixed task prompt, a bounded base64 XWD capture with its SHA-256, and a bounded X11 window tree. The agent receives no Docker path, host path, API key, or other process environment.

The strict response is one of:

```json
{"type":"type_text","text":"printable text, at most 256 UTF-8 bytes"}
{"type":"key","key":"ENTER"}
{"type":"finish"}
```

`TAB` and `ESCAPE` are also allowed keys. Unknown fields, control characters, responses over 4 KiB, and every other action fail closed. Text and keys are passed as fixed `xdotool` argument arrays inside the restricted application container. They are never evaluated by a host shell. This reviewed application is a purpose-built input reader rather than a command shell.

The loop allows at most six steps and uses the manifest's 45-second wall limit. Each screen is limited to 2,500,000 bytes, each window tree to 1 MiB, the HTTP request to 4 MiB, and all run artifacts to 20 MiB. It retains observation metadata and screenshots, the exact bounded agent responses, normalized actions, application transcript, upstream verifier output, and a receipt with their hashes. A successful task requires all of: an agent `finish`, the exact application transcript, the real terminal window and screen evidence, and the immutable upstream verifier returning true with score 100. `finish` alone cannot pass. Confirmed task misses map to `passed: false`; protocol, runtime, evidence, or cleanup failures throw so the leased runner reports `passed: null`.

The deterministic fixture validates every observation hash and then returns the reviewed three-action sequence. It proves the real boundary and isolated application execution; it is not an AI/model result:

```bash
mkdir -p /absolute/protected/gym-agent-runs
python3.13 sandbox/examples/deterministic-agent.py --port 8765

PYTHONPATH=sandbox/src PYTHONDONTWRITEBYTECODE=1 python3.13 \
  -m lotuspond_sandbox.agent_loop \
  --bundle sandbox/real-app \
  --output-dir /absolute/protected/gym-agent-runs \
  --agent-url http://127.0.0.1:8765/v1/action
```

For product ingestion, create a scenario whose input has exactly these two fields:

```json
{
  "gym_environment_id": "example.basic_x11@0.1",
  "gym_task_id": "hello@1"
}
```

Then run the existing leased runner with a scoped runner key. The API credential remains in the Node process and is removed from the controller child environment:

```bash
LOTUSPOND_API_URL=http://127.0.0.1:8000/api/v1 \
LOTUSPOND_API_KEY='<runner-scoped-key>' \
LOTUSPOND_RUNNER_ID='<registered-runner-uuid>' \
LOTUSPOND_GYM_AGENT_URL=http://127.0.0.1:8765/v1/action \
LOTUSPOND_GYM_RUNS_DIR=/absolute/protected/gym-agent-runs \
npm run runner -- ./sandbox/leased-run-adapter.mjs
```

The adapter pins the scenario identifiers, source bundle, runtime image, upstream revision, task, verifier, controller module, and security expectations. Scenario content cannot replace the destination, image, Python module, command, or runtime policy. `LOTUSPOND_GYM_PYTHON` may select an operator-installed Python executable; it does not accept per-run code or arguments. The customer execution archive copies this kit and the reviewed application image; it does not put Docker access in the HTTP/MCP/voice image.

## Operator configuration

Copy `manifests/gym-reviewed.example.json` into a protected manifest directory as `<manifest_id>.json`, replace the all-zero image id, reduce the task list to reviewed tasks, and make the file owner-writable only. Configure:

```text
LOTUSPOND_SANDBOX_MANIFEST_DIR=/absolute/protected/manifests
LOTUSPOND_SANDBOX_RUNS_DIR=/absolute/protected/runs
LOTUSPOND_SANDBOX_AUTH_KEY=<32-to-64-random-bytes-as-lowercase-hex>
```

The runtime user must be non-root and able to access Docker. The runs directory must already exist. The launcher creates a new `0700` directory per run and refuses an existing run id, which blocks accidental replay.

## Authenticated launch contract

The trusted backend or operator first issues a five-minute HMAC grant bound to the manifest, environment, task, run id, steps, seed, expiry, and nonce. A public API must authenticate and authorize the workspace before issuing this grant; the `authorize` command is a worker/admin boundary and must never be exposed as an unauthenticated API operation.

```bash
lotuspond-sandbox authorize \
  --manifest-id gym-reviewed --environment-id moodle --task-id enroll_student \
  --run-id run-one --steps 5 --seed 42 --grant-file /protected/grants/run-one.json

lotuspond-sandbox run --manifest-id gym-reviewed --grant-file /protected/grants/run-one.json
```

The worker passes only fixed arguments to Docker and Gym Anything. It runs with `--network=none`, a read-only root, no capabilities, `no-new-privileges`, the caller's non-root uid/gid, process/memory/CPU/time limits, a bounded temporary filesystem, and one artifact bind mount. The Docker image is used with `--pull=never`.

Standard output and error are discarded because upstream output may contain prompts or credentials. Gym Anything artifacts are retained under the run directory and must be redacted, access-controlled, and expired according to the project policy. The byte limit is checked after the run; enforce a filesystem quota when a hard write-time artifact limit is required.

On timeout or interruption, the launcher force-removes the named container and queries the daemon to confirm it is absent. A timeout receipt is written only after confirmed cleanup; otherwise the receipt status is `cleanup_failed` and operators must reconcile the named container before retrying. Artifact accounting rejects symbolic links and non-regular files instead of following workload-created paths outside the run directory.

`lotuspond-sandbox doctor` performs a read-only Docker daemon version check. It does not launch a workload.

## Local validation evidence

On 2026-09-21, the complete manifest, HMAC grant, launcher, artifact, and receipt path ran the reviewed `moodle_env@0.1` task `enroll_student@1` with seed 42 and the pinned image id `sha256:c81d4f9ecaae865fcc904abf8ecdb01cdfbaa089da2fceb9568b3fc65aa359ab`. Docker 29.5.2 ran it with no network, read-only root, no capabilities, `no-new-privileges`, a non-root uid, and the configured CPU, memory, process, temporary-filesystem, and timeout bounds. The process exited zero and the Lotuspond receipt recorded runner `local`, command SHA-256 `2125ef91cce529834b8bee3aa7f8ff411633bf5ad9eed9738d94b9da5ef02fc4`, and 1,205 artifact bytes. The independent Gym summary decided the task and returned `passed: false`, score 0, because the synthetic local runner produced no application result. This is local pipeline evidence only; it is explicit evidence that a real Moodle application was not exercised.

Also on 2026-09-21, the separate real terminal smoke ran upstream `example.basic_x11@0.1` / `hello@1` using application image `sha256:2c37be8d20e6a6b205dbe071624b5154d1307034dc771e857fa10b7208b4eb7c`. Run `real-78ef91fa99b5af4f` completed in one second. The pinned upstream verifier returned `passed: true`, score 100; independent checks confirmed the exact `Hello from Lotuspond` terminal transcript, the `Gym Anything Hello` X11 window, and a 1,923,179-byte screen capture. Effective runtime-policy inspection passed every required control, and the controller confirmed the exact labeled container was absent after removal. This is a real local xterm smoke only. It does not establish Moodle, browser, mobile, hosted, or arbitrary Gym environment coverage.

## Evidence boundary

The manifest proves an operator reviewed an allowlist and selected an immutable image. The grant authenticates a short-lived run request to this worker. The receipt records the image, generic outcome, Docker server version, resource policy, and a digest of the exact argv. None of these prove the task verifier is correct, the application is safe, the image was scanned, the host is isolated, or the artifacts are free of sensitive data.

A later schema-v6 product integration used the bounded agent loop and actual leased runner. Run `1aca3b87-c45f-4c73-936b-0231f26d6941` persisted `passed: true` with unavailable cost, and receipt SHA-256 `19d0a78caea003c573910ef37019eff0b5f0d83fe456c45c07f79e823f3e507a` matched the retained receipt exactly. The deterministic adapter supplied three actions; four screen observations, exact terminal state, upstream verifier success, isolation checks, and cleanup were retained. The disposable scoped runner key was revoked after validation. This is local integration evidence, not an AI-model success claim.
