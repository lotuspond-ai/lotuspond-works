# Lotuspond evidence manifest CLI

`lotuspond-evidence` creates and verifies deterministic SHA-256 manifests for a directory of regular artifact files, and signs them so a verifier can tell who published them.

```text
lotuspond-evidence create <artifact-directory> <manifest.json>
lotuspond-evidence verify <artifact-directory> <manifest.json>
lotuspond-evidence keygen <private-key.pem> <public-key.pem>
lotuspond-evidence sign <manifest.json> <private-key.pem> <signature.json>
lotuspond-evidence verify-signed <artifact-directory> <manifest.json> <signature.json> <trusted-public-key.pem>
```

## Manifests

Entries use sorted, UTF-8, `/`-separated relative paths. Verification fails for changed, missing, or extra files, unsafe paths, unsupported schema values, and symbolic links. If the manifest is inside the artifact directory it is excluded from the file set.

A manifest alone proves file integrity relative to a separately trusted checksum; `verify` makes no claim about who produced it.

## Publisher signatures

`keygen` creates an Ed25519 key pair as a PKCS#8 private key, written owner-only (0600), and a SubjectPublicKeyInfo public key, both PEM. Neither file may already exist, and `sign` refuses a private key that other users can read. `sign` checks that the manifest is well formed, then signs its exact bytes, prefixed with a context line so the signature cannot stand for anything else:

```text
message = "lotuspond-evidence-manifest-v1\n" || manifest bytes
```

The signature file records the algorithm, the context, the signer's `key_id` (`sha256:` and the SHA-256 of the raw 32-byte public key), the manifest's SHA-256 and the 64-byte signature in hex. It may not already exist.

`verify-signed` trusts only the public key it is given. It refuses a signature made by any other key and names both key ids, refuses a signature for a different manifest, checks the signature strictly (small-order keys and malleable signatures are rejected), and then verifies the very bytes it checked against the artifact directory. Distribute the publisher's public key, or its `key_id`, through a channel the verifier already trusts; the `key_id` inside a signature file identifies a key but never makes it trusted.

Because the keys are standard PEM and the message is fully specified, a verifier does not need this tool:

```sh
jq -r .signature manifest.sig.json | xxd -r -p > signature.bin
{ printf 'lotuspond-evidence-manifest-v1\n'; cat manifest.json; } > message.bin
openssl pkeyutl -verify -pubin -inkey publisher.pub -rawin -in message.bin -sigfile signature.bin
```

Signatures establish which key published a manifest. They provide no timestamp authority, key revocation, transparency log, or hardware key custody: protect the private key yourself, and publish a new public key and re-sign when rotating. Ed25519 comes from [`ed25519-dalek`](https://github.com/dalek-cryptography/curve25519-dalek); key generation reads the operating system's random source through `getrandom`.
