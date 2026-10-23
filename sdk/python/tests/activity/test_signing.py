"""Frozen vectors and independent Node verification of the complete signing frame."""

import copy
import json
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from lotuspond_sdk import SdkError, TrustSet
from lotuspond_sdk.activity import (
    ActivitySignature,
    ActivityVerificationReport,
    canonical_activity_json,
    sign_activity,
    verify_activity,
)
from lotuspond_sdk.executable import ExecutableError

HERE = Path(__file__).with_name("fixtures")
SEED = "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
PUBLIC = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
KEY_ID = "21fe31dfa154a261626bf854046fd2271b7bed4b6abe45aa58877ef47f9721b9"


def raw():
    return json.loads((HERE / "read.json").read_bytes())


def signature():
    return json.loads((HERE / "read.signature.json").read_bytes())


def key():
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(SEED))


def trust(state="allowed"):
    return {"format_version": 1, "keys": [
        {"key_id": KEY_ID, "public_key": PUBLIC, "state": state}
    ]}


def test_literal_vector_and_distinct_frame():
    canonical = canonical_activity_json(raw())
    assert len(canonical) == 1375
    assert sign_activity(raw(), key()).model_dump() == signature()
    report = verify_activity(raw(), signature(), TrustSet.model_validate(trust()))
    assert report.model_dump() == {
        "valid": True, "code": "verified", "sha256": signature()["sha256"], "key_id": KEY_ID,
        "binding_status": "not_checked", "authority_granted": False,
    }
    assert report.authority_granted is False
    result = subprocess.run(["node", "-e", r'''
const fs = require('node:fs'), crypto = require('node:crypto');
const [rawPath, canonicalPath, signaturePath] = process.argv.slice(1);
const raw = JSON.parse(fs.readFileSync(rawPath));
function ordered(v) {
  if (v && typeof v === 'object' && !Array.isArray(v))
    return Object.fromEntries(Object.keys(v).sort().map(k => [k, ordered(v[k])]));
  return v;
}
const c = Buffer.from(JSON.stringify(ordered(raw)));
if (!c.equals(fs.readFileSync(canonicalPath))) throw Error('canonical bytes');
const s = JSON.parse(fs.readFileSync(signaturePath));
if (crypto.createHash('sha256').update(c).digest('hex') !== s.sha256) throw Error('digest');
const header = {...s}; delete header.signature;
const h = Buffer.from(JSON.stringify(ordered(header)));
const length = n => {const b=Buffer.alloc(8); b.writeBigUInt64BE(BigInt(n)); return b;};
const frame = context => Buffer.concat([Buffer.from(context), Buffer.from([0]), length(h.length),
                                       h, length(c.length), c]);
const public = crypto.createPublicKey({format:'der', type:'spki', key:Buffer.from(
  '302a300506032b6570032100d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a', 'hex')});
if (!crypto.verify(null, frame(s.context), public, Buffer.from(s.signature,'hex'))) throw Error('signature');
for (const other of ['lotuspond-runtime-command-v1','lotuspond-workflow-package-signature-v2'])
  if (crypto.verify(null,frame(other),public,Buffer.from(s.signature,'hex'))) throw Error('context');
console.log(JSON.stringify({bytes:c.length,sha256:s.sha256,verified:true}));
''', str(HERE / "read.json"), str(HERE / "read.canonical.json"),
        str(HERE / "read.signature.json")], check=True, text=True, capture_output=True, timeout=10)
    assert json.loads(result.stdout)["verified"] is True


def test_signature_covers_every_field_and_current_trust_wins():
    source = raw()
    for name, value in source.items():
        changed = copy.deepcopy(source)
        if name in ("schema_version", "kind"):
            continue  # These have no alternative valid value without a correlated update.
        if name == "dependency":
            changed[name]["name"] = "another-reader"
        elif name == "input":
            changed[name]["sha256"] = "f" * 64
        elif name == "issued_at_ms":
            changed[name] += 1
        elif name == "expires_at_ms":
            changed[name] -= 1
        elif type(value) is int:
            changed[name] += 1
        elif name.endswith("sha256"):
            changed[name] = "f" * 64
        elif name.endswith("_id") and name != "installation_id":
            changed[name] = "ffffffff-ffff-4fff-8fff-ffffffffffff"
        else:
            changed[name] = "another-value"
        assert verify_activity(changed, signature(), trust()).code == "digest_mismatch", name
    model = copy.deepcopy(source)
    model.update(kind="model", operation=None)
    model["dependency"]["kind"] = "model"
    assert verify_activity(model, signature(), trust()).code == "digest_mismatch"
    assert verify_activity(model, sign_activity(model, key()), trust()).valid
    for nested, field, value in (("input", "id", "ffffffff-ffff-4fff-8fff-ffffffffffff"),
                                 ("input", "version", 2),
                                 ("dependency", "version", "1.0.1"),
                                 ("dependency", "sha256", "f" * 64)):
        changed = copy.deepcopy(source)
        changed[nested][field] = value
        assert verify_activity(changed, signature(), trust()).code == "digest_mismatch"
    assert verify_activity(source, signature(), {"format_version": 1, "keys": []}).code == "untrusted_key"
    assert verify_activity(source, signature(), trust("revoked")).code == "revoked_key"
    changed = {**signature(), "signature": "0" * 128}
    assert verify_activity(source, changed, trust()).code == "invalid_signature"
    assert verify_activity({**source, "operation": "another"}, changed, trust("revoked")).code == "revoked_key"


def test_old_contexts_and_sender_keys_are_rejected():
    old = json.loads((HERE.parents[1] / "fixtures/executable-v2/signing-v2.json").read_bytes())
    old_v1 = json.loads((HERE.parents[1] / "fixtures/signing-v1.json").read_bytes())
    for variant in (old_v1, old["command"]["envelope"], old["package"]["envelope"],
                    {**signature(), "public_key": PUBLIC},
                    {**signature(), "format_version": True},
                    {**signature(), "format_version": 1.0}):
        with pytest.raises(ExecutableError):
            verify_activity(raw(), variant, trust())
    malformed = trust()
    malformed["keys"][0]["public_key"] = "0" * 64
    with pytest.raises(SdkError, match="independent trust"):
        verify_activity(raw(), signature(), malformed)
    with pytest.raises(SdkError):
        sign_activity(raw(), object())


def test_mutated_signature_trust_and_report_are_revalidated():
    detached = ActivitySignature.model_validate(signature())
    object.__setattr__(detached, "format_version", True)
    with pytest.raises(ExecutableError):
        verify_activity(raw(), detached, trust())
    trusted = TrustSet.model_validate(trust())
    object.__setattr__(trusted.keys[0], "state", "revoked")
    assert verify_activity(raw(), signature(), trusted).code == "revoked_key"
    object.__setattr__(trusted.keys[0], "key_id", "0" * 64)
    with pytest.raises(SdkError):
        verify_activity(raw(), signature(), trusted)
    report = verify_activity(raw(), signature(), trust())
    with pytest.raises(ValidationError):
        report.authority_granted = True
    for update in ({"authority_granted": True}, {"authority_granted": 0},
                   {"valid": False}, {"binding_status": "verified"}):
        with pytest.raises(ValidationError):
            ActivityVerificationReport(**{**report.model_dump(), **update})
