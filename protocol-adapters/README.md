# Lotuspond protocol runner adapters

This isolated package lets the local Lotuspond runner call one explicitly configured MCP tool over Streamable HTTP. It does not let scenario input select an endpoint or tool, and it does not ask a model to select tools.

## Operator configuration

Use Node.js 22.12 or newer, then install this package from its own directory:

```sh
cd protocol-adapters
npm ci --ignore-scripts
```

Set these variables in the runner process. Do not put the bearer token in scenario input, source control, command-line arguments, or logs.

| Variable                           | Required                      | Meaning                                                                                                                                                                       |
| ---------------------------------- | ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `LOTUSPOND_MCP_ENDPOINT`           | yes                           | Operator-approved Streamable HTTP endpoint. HTTPS is required except for loopback HTTP used in local development. URL credentials, query strings, and fragments are rejected. |
| `LOTUSPOND_MCP_TOOL`               | for execution                 | Exact tool name the adapter may call.                                                                                                                                         |
| `LOTUSPOND_MCP_TOKEN`              | when the endpoint requires it | Static bearer token read before each request by the official SDK auth provider.                                                                                               |
| `LOTUSPOND_MCP_TIMEOUT_MS`         | no                            | Whole-operation and request timeout, 250–60,000 ms; default 20,000.                                                                                                           |
| `LOTUSPOND_MCP_MAX_RESPONSE_BYTES` | no                            | Maximum bytes in each HTTP response, 1 KiB–4 MiB; default 1 MiB.                                                                                                              |
| `LOTUSPOND_MCP_MAX_OUTPUT_BYTES`   | no                            | Maximum retained tool output, 1 KiB–512 KiB; default 128 KiB.                                                                                                                 |
| `LOTUSPOND_NETWORK_MODE`           | no                            | Set to `airgap` to refuse the connection before constructing a client.                                                                                                        |

The endpoint is exact: redirects and SDK requests to any other URL are refused. The adapter sends no proxy configuration. Authentication failures and insufficient scope become safe error codes; token values and provider response bodies are never logged.

## Runner contract

Pass the adapter path to the existing root runner:

```sh
npm run runner -- ./protocol-adapters/src/mcp-runner.mjs
```

The packaged execution image equivalent is `docker run --rm --env-file /protected/execution.env IMAGE_ID runner-mcp`.

Scenario input has one accepted shape:

```json
{
  "arguments": {
    "prompt": "Review this release evidence"
  }
}
```

The adapter calls only `LOTUSPOND_MCP_TOOL`. Each expectation must contain exactly one supported field: one non-empty string `contains`, or one JSON-valued `equals`. Empty, ambiguous, or extended expectation objects return `passed: null`. Transport, timeout, cancellation, authentication, authorization, oversized-response, and MCP tool-error outcomes also return `passed: null`; they never become a passing verdict. Tool text and structured output are bounded. Any truncation, invalid result, unsupported content block, or summarized base64 media produces `passed: null` with `outputComplete: false` and explicit `outputIssues` provenance, so partial evidence cannot pass.

The runner contract currently requires a numeric `costUsd`, so this adapter returns the transport placeholder `0` while setting `evidence.cost_measurement` to `unavailable`. The runner preserves that marker, persists a null cost metric, and a configured maximum-cost gate blocks when cost is unavailable. MCP tool results do not provide a standard provider-cost field, so the placeholder is never treated as a measured zero.

## Read-only capability inspection

Tool discovery is separate from execution:

```sh
cd protocol-adapters
LOTUSPOND_MCP_ENDPOINT=https://mcp.example.test/service \
LOTUSPOND_MCP_TOKEN=... \
node src/mcp-tools.mjs
```

This command runs MCP `tools/list` and prints bounded names, titles, and descriptions. A `status` of `advertised` means only that the endpoint returned those entries in that session. It does not prove that a tool is trustworthy, registered with Lotuspond, correctly authorized, or executable. The execution adapter never calls `tools/list` automatically.

## Evidence and limits

The package pins `@modelcontextprotocol/client` 2.0.0. On 2026-09-21, the implementation was checked against the official SDK's [first-client guide](https://github.com/modelcontextprotocol/typescript-sdk/blob/main/docs/get-started/first-client.md), [machine authentication guide](https://github.com/modelcontextprotocol/typescript-sdk/blob/main/docs/clients/machine-auth.md), and [Streamable HTTP client source](https://github.com/modelcontextprotocol/typescript-sdk/blob/main/packages/client/src/client/streamableHttp.ts), plus the official MCP [Streamable HTTP transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports). The SDK documents `Client`, `StreamableHTTPClientTransport`, `listTools()`, `callTool()`, per-request abort signals, and bearer tokens supplied by an auth provider.

Tests use local loopback mock MCP servers and injected mock clients. They cover the initialization/call contract, bearer header, explicit tool selection, separate read-only listing, cancellation, airgap refusal, redirect refusal, response bounds, provider tool errors, and omission of base64 media. No customer MCP endpoint or live provider was contacted, so availability, endpoint-specific schemas, authentication policy, and production registration remain unverified.

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
| `LOTUSPOND_NETWORK_MODE`            | no       | `connected` (default) or `airgap`. For this HTTP adapter, airgap permits only verified loopback destinations and rejects other hosts before DNS.                                                                                                   |

The only accepted scenario input is `{ "prompt": "Your instruction" }`. Extra fields are rejected, so scenarios cannot choose a destination, headers, model, system prompt, or request mode. Plain mode sends `{ "input": "Your instruction" }`. Chat mode sends a user message, the optional operator system message, and the optional operator model field. It does not create an agent loop or select tools.

Responses must be uncompressed UTF-8 `text/plain` or JSON (`application/json` or an `application/*+json` media type). A configured response path must contain non-empty text. Without one, the adapter accepts a JSON string or the first non-empty string at `output_text`, `text`, `output`, `reply`, `answer`, `response`, `content`, `message.content`, `choices[0].message.content`, or `choices[0].text`, in that order. Use an explicit path when the endpoint has multiple output fields. Plain text responses are used directly.

Expectations contain exactly one non-empty string `contains` or `equals`. Checks are case-sensitive and evaluate response text only; they do not verify an external business outcome. A complete output that contradicts a supported assertion returns `passed: false`. Missing or unsupported criteria return `passed: null` with `outcome: unassessed`. Missing output, malformed responses, redirects, cancellation, timeout, HTTP errors, and bounds violations return `passed: null` with a safe error code; partial output never passes. A response echoing the configured bearer token is discarded. Other output can contain customer data, so the endpoint must redact confidential content before returning evidence.

Every HTTP result sets `evidence.cost_measurement: unavailable` and omits `costUsd`. The adapter does not infer billing from response fields. A maximum-cost release gate therefore remains blocked until the required cost evidence is available from an appropriate source.

HTTP requests use explicit direct agents without ambient proxies, require normal certificate verification for HTTPS, and never follow redirects. Airgap here constrains this adapter's destination; it does not prove that an operator's loopback service has no onward network access. The runner's process isolation and the installation's network controls remain separate boundaries.

Focused tests use disposable loopback HTTP servers, never a customer endpoint. They cover both request mappings, bearer authentication, output paths, unknown evidence, request/response bounds, trickled-body deadlines, cancellation, redirect refusal, DNS pinning, airgap restrictions, proxy isolation, and sensitive-output rejection. Run `node --test test/http-adapter.test.mjs` from this package or `npm run test:protocol` from the repository root.

Recovery provenance: the request modes and response-selection concepts were reviewed in the archived `lotuspond-main-server/packages/g2-server/src/agent-gym/http-endpoint-agent.service.ts`. This adapter is a bounded customer-side implementation using current Lotuspond contracts; it does not restore the old server, verdict mapping, stored credentials, integrations, or dependencies. No external endpoint execution has been verified.

## A2A

`src/a2a-runner.mjs` evaluates an operator-configured A2A agent. It sends JSON-RPC 2.0 `message/send` to `LOTUSPOND_A2A_ENDPOINT` with the scenario input as a single text part, over the same hardened transport the HTTP adapter uses: pinned DNS resolution, no ambient proxy, TLS verification kept on, redirects refused, identity encoding only, and a bounded response inside a whole-operation deadline. Remote endpoints must use HTTPS, and airgap mode refuses a non-loopback endpoint before any socket opens.

**Only a completed task is judged.** A task still `working`, `input-required` or `auth-required` is `task_unfinished`; a `failed`, `canceled` or `rejected` task is `task_not_completed`. Both stay unknown with the state recorded, never a pass and never a failure, because an unfinished conversation is a fact about the exchange rather than an answer to assess. A direct agent `message` reply counts as an answer. A JSON-RPC error keeps its numeric code and drops the agent's message text, which may describe internals.

Expectations are the same exact `contains` and `equals` the other adapters support; anything else stays `unassessed`. A2A carries no billing measurement, so cost is `unavailable` rather than a measured zero. Streaming (`message/stream`), task resumption and push notifications are not implemented. These are local contract tests against a loopback server; no customer A2A endpoint has been validated.
