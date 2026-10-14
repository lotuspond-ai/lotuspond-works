# Canonical byte provenance

`reviewed-action.canonical.json` was frozen using the backend validator at commit
753b0658730a5ac1f5ffd47afab89f30b0d7374e before SDK extraction, from
`contracts/examples/reviewed-action.v1.json`. It contains exact sorted-key UTF-8
canonical bytes, without a trailing newline. SHA-256:
`305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3`.
The fixture is an independent regression oracle; tests must not regenerate it.
