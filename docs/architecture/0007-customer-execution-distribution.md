# 0007: Customer-side execution distribution

Status: accepted, 2026-09-21.

The service offline bundle packages the API, UI, evaluation worker, collector, PostgreSQL and Redis. Customer-side execution tools were still operated from a source checkout: the Node runner, HTTP and MCP adapters, the voice bridge, and the Gym real-application controller. That gap blocked offline transfer of the tools that produce evaluation evidence.

Those tools now travel in a second archive. A nonroot Node 24.21.0 image contains the runner, API client, release gate, HTTP/MCP adapters with locked production dependencies, and the voice bridge. Its commands are `runner-http`, `runner-mcp`, `voice`, and `gate`. That image has no Docker socket, Python runtime, or baked credential. HTTP, MCP and voice work without a repository bind mount.

Gym remains a host-side kit. The archive includes the reviewed xterm application image plus the Node leased-run adapter, the Python standard-library controller, and the pinned task, verifier, license and provenance files. Host Python and Docker are required to launch that task. Host Node is required only for the leased API adapter. The general runner image is not given Docker-host authority.

`LOTUSPOND_API_URL` accepts either an origin or a base that already ends in `/api/v1`. Voice ingest and the API client share that rule. Voice ingest and provider sockets use direct transports that ignore ambient proxies and keep TLS verification enabled.

This is packaging and local installation evidence. It does not replace live provider, customer-workload, clean-host transfer, or production-capacity proof.
