# Canonical byte provenance

`reviewed-action.canonical.json` was frozen using the backend validator at commit
753b0658730a5ac1f5ffd47afab89f30b0d7374e before SDK extraction, from
`contracts/examples/reviewed-action.v1.json`. It contains exact sorted-key UTF-8
canonical bytes, without a trailing newline. SHA-256:
`305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3`.
The fixture is an independent regression oracle; tests must not regenerate it.


## Detached signature vector provenance

`signing-v1.json` was independently produced with **OpenSSL 3.5.7 (9 June 2026)**
on 14 October 2026 using RFC 8032 section 7.1 test vector 1’s publicly known,
synthetic Ed25519 secret seed:
`9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60`.
Its raw public key is
`d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a`.
**This published test key must never be used for real publisher trust.**

The fixture signs the historical `reviewed-action.canonical.json` bytes without a
newline. Header `H` is sorted-key compact ASCII JSON of all fixture fields except
`signature` (361 bytes); canonical content `C` is 1,166 bytes. The exact frame is:

```python
frame = (b"lotuspond-workflow-package-signature-v1\x00"
         + len(H).to_bytes(8, "big") + H
         + len(C).to_bytes(8, "big") + C)
```

The total frame is 1,583 bytes. Header generation used Python’s standard-library
`json.dumps(header, sort_keys=True, ensure_ascii=True, separators=(",", ":"))`
and `hashlib.sha256`; no SDK signing/framing function was used. The RFC seed was
encoded as this published PKCS8 test PEM in a temporary local file:

```text
-----BEGIN PRIVATE KEY-----
MC4CAQAwBQYDK2VwBCIEIJ1hsZ3v/VpguoRK9JLsLMREScVpezJpGXA7rAMcrn9g
-----END PRIVATE KEY-----
```

OpenSSL generated the expected 64-byte signature, stored as lowercase hex:

```sh
openssl pkeyutl -sign -rawin -inkey private.pem -in frame -out signature
```

The test checks the committed literal fixture, verifies SDK-produced signatures
with `openssl pkeyutl -verify -rawin -pubin`, and verifies independently generated
OpenSSL signatures with the SDK. OpenSSL subprocesses are bounded to ten seconds.
Missing OpenSSL is a failure, including in the Linux Python 3.13/3.14 CI matrix,
not a skipped cross-verification test. RFC vector 1 separately checks the known
empty-message signature and raw key/PKCS8 encoding. All keys are synthetic;
production/customer keys and credentials were never accessed.


## Trust-key encoding guard provenance

The seven small-order y-encoding constants and sign-bit masking convention are
from [libsodium 1.0.18, `ge25519_has_small_order`, lines 1022–1075](https://github.com/jedisct1/libsodium/blob/1.0.18/src/libsodium/crypto_core/ed25519/ref10/ed25519_ref10.c#L1022).
The source was read directly from that version’s public raw file on 14 October
2026. SHA-256 of the retrieved source:
`40291bba865beab9817381460cda1d977a106c11cf4c393f854e25979d84bfc7`. Its published constants
cover y=0, y=1, the two order-8 y values, p-1, p and p+1, testing both sign bits.
The explicit canonical bound y < p=2^255-19 follows [RFC 8032 section 5.1.3](https://www.rfc-editor.org/rfc/rfc8032#section-5.1.3).
All 19 representable noncanonical y values (p through 2^255-1), under both sign
bits, are tested. Normal RFC public keys with either sign bit remain admissible.

On local cryptography 50.0.2, an identity-point public key (`01` followed by 31
zero bytes) accepted the forged signature R=identity/S=0 (`01` followed by 63
zero bytes) for arbitrary bytes. The SDK now rejects that trust record with
`invalid_trust_set`, before it can authorize package verification. This guard
uses established encoding constants and an integer encoding bound; it does not
implement curve arithmetic or full subgroup validation and does not assert
complete equivalence to ed25519-dalek’s `verify_strict`.
