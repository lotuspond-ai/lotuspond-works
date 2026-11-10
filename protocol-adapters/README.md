# Lotuspond HTTP runner adapter

This isolated package lets the local Lotuspond runner evaluate one operator-configured agent behind HTTP against frozen scenarios. Scenario input cannot select the endpoint, headers or credentials. The MCP and A2A adapters were retired on 2026-11-11 ([ADR-0014](../docs/architecture/0014-focus-on-the-core-loop.md)); the package now has no dependencies.

## HTTP endpoint adapter

`src/http-runner.mjs` calls one operator-configured agent endpoint using a JSON POST. It uses Node's built-in HTTP clients and requires no additional dependency. Use it with the root runner:

```sh
npm run runner -- ./protocol-adapters/src/http-runner.mjs
```

The packaged execution image uses the same adapter without a repository path: `docker run --rm --env-file /protected/execution.env IMAGE_ID runner-http`.

Configure the endpoint and optional bearer token in the runner's environment. Never put credentials in a URL, scenario, shell command argument, or source control.

| Variable                            | Required | Meaning                                                                                                                                                                                                                                            |
| ----------------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `LOTUSPOND_HTTP_ENDPOINT`           | yes      | Exact endpoint URL. HTTPS is required except for literal loopback addresses or `localhost`, whose DNS answers must all be loopback and whose selected address is pinned for the connection. Credentials, query strings, and fragments are refused. |
| `LOTUSPOND_HTTP_MODE`               | no       | `plain-input` (default) or `openai-chat`.                                                                                                                                                                                                          |
| `LOTUSPOND_HTTP_TOKEN`              | no       | Optional static bearer token. Arbitrary headers and scenario-supplied authentication are unsupported.                                                                                                                                              |
| `LOTUSPOND_HTTP_RESPONSE_PATH`      | no       | Exact response text path, such as `result.reply` or `choices[0].message.content`. Up to 16 own-property/index segments; inherited properties are never read.                                                                                       |
| `LOTUSPOND_HTTP_MODEL`              | no       | Model field supplied only in `openai-chat` mode. This is request configuration, not proof of which model executed.                                                                                                                                 |
| `LOTUSPOND_HTTP_SYSTEM_PROMPT`      | no       | System message supplied only in `openai-chat` mode; at most 32 KiB.                                                                                                                                                                                |
| `LOTUSPOND_HTTP_TIMEOUT_MS`         | no       | Entire operation, including DNS, connection, headers, and full response body: 250–60,000 ms, default 20,000. Runner cancellation also destroys the active request.                                                                                 |
| `LOTUSPOND_HTTP_MAX_INPUT_BYTES`    | no       | Bound for the complete encoded JSON request: 1–256 KiB, default 64 KiB.                                                                                                                                                                            |
| `LOTUSPOND_HTTP_MAX_RESPONSE_BYTES` | no       | Maximum complete HTTP response body: 1 KiB–2 MiB, default 1 MiB.                                                                                                                                                                                   |
| `LOTUSPOND_HTTP_MAX_OUTPUT_BYTES`   | no       | Maximum encoded retained `{text}` output: 1–256 KiB, default 64 KiB.                                                                                                                                                                               |

The only accepted scenario input is `{ "prompt": "Your instruction" }`. Extra fields are rejected, so scenarios cannot choose a destination, headers, model, system prompt, or request mode. Plain mode sends `{ "input": "Your instruction" }`. Chat mode sends a user message, the optional operator system message, and the optional operator model field. It does not create an agent loop or select tools.

Responses must be uncompressed UTF-8 `text/plain` or JSON (`application/json` or an `application/*+json` media type). A configured response path must contain non-empty text. Without one, the adapter accepts a JSON string or the first non-empty string at `output_text`, `text`, `output`, `reply`, `answer`, `response`, `content`, `message.content`, `choices[0].message.content`, or `choices[0].text`, in that order. Use an explicit path when the endpoint has multiple output fields. Plain text responses are used directly.

Expectations contain exactly one non-empty string `contains` or `equals`. Checks are case-sensitive and evaluate response text only; they do not verify an external business outcome. A complete output that contradicts a supported assertion returns `passed: false`. Missing or unsupported criteria return `passed: null` with `outcome: unassessed`. Missing output, malformed responses, redirects, cancellation, timeout, HTTP errors, and bounds violations return `passed: null` with a safe error code; partial output never passes. A response echoing the configured bearer token is discarded. Other output can contain customer data, so the endpoint must redact confidential content before returning evidence.

Every HTTP result sets `evidence.cost_measurement: unavailable` and omits `costUsd`. The adapter does not infer billing from response fields. A maximum-cost release gate therefore remains blocked until the required cost evidence is available from an appropriate source.

HTTP requests use explicit direct agents without ambient proxies, require normal certificate verification for HTTPS, and never follow redirects. The runner's process isolation and the installation's network controls remain separate boundaries.

Focused tests use disposable loopback HTTP servers, never a customer endpoint. They cover both request mappings, bearer authentication, output paths, unknown evidence, request/response bounds, trickled-body deadlines, cancellation, redirect refusal, DNS pinning, proxy isolation, and sensitive-output rejection. Run `node --test test/http-adapter.test.mjs` from this package or `npm run test:protocol` from the repository root.

Recovery provenance: the request modes and response-selection concepts were reviewed in the archived `lotuspond-main-server/packages/g2-server/src/agent-gym/http-endpoint-agent.service.ts`. This adapter is a bounded customer-side implementation using current Lotuspond contracts; it does not restore the old server, verdict mapping, stored credentials, integrations, or dependencies. No external endpoint execution has been verified.
