# Customer-side execution image

This image is separate from the API/UI image. It runs evaluation work inside the customer's own boundary: the supervised HTTP runner that evaluates an agent against frozen scenarios, and the release gate a delivery pipeline obeys. It carries no secrets, customer data, API credentials, or proof of production readiness.

The entrypoints are `runner-http` and `gate`. They do not mount a repository, the Docker socket, or a Python runtime, and they contain no baked credentials. Do not grant the runner access to the Docker socket.

## Build and run

```sh
docker build -f deploy/execution/Dockerfile -t lotuspond-execution:reviewed .
docker run --rm --env-file /protected/execution.env lotuspond-execution:reviewed runner-http
docker run --rm --env-file /protected/execution.env lotuspond-execution:reviewed gate RELEASE_ID
```

Copy `runtime.env.example` to a protected path outside the repository, replace every placeholder, and set its permissions to owner read/write only. `LOTUSPOND_API_URL` may be the API origin or the `/api/v1` base; remote URLs must use HTTPS. `gate` exits with status 2 for any decision other than a pass.

## Check the image

```sh
python3 deploy/checks/execution-smoke.py --image lotuspond-execution:reviewed
```

The check confirms the image's nonroot user, entrypoint and help, that it contains no Python runtime, Docker client or socket, and that the HTTP runner passes a synthetic case against an in-image loopback endpoint. It runs with no network, publishes no ports, uses synthetic data only, and label-checks cleanup. Passing locally does not prove live agent behavior or production capacity.
