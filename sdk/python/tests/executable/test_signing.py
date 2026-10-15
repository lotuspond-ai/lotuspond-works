"""Independent RFC/Node/OpenSSL evidence for domain-separated local signatures."""

import copy
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from pydantic import ValidationError

from lotuspond_sdk import SdkError, TrustSet, sign_package, verify_package
from lotuspond_sdk.executable import (
    CommandSignatureEnvelope,
    ExecutableError,
    ExecutableSignatureEnvelope,
    ExecutableVerificationReport,
    build_action_intent,
    canonical_executable_json,
    canonical_wire_json,
    check_command_binding,
    command_signature_envelope_json,
    executable_signature_envelope_json,
    executable_verification_report_json,
    sign_executable_package,
    sign_runtime_command,
    validate_command_signature_envelope,
    validate_executable_signature_envelope,
    validate_executable_verification_report,
    validate_runtime_command,
    verify_executable_package,
    verify_runtime_command,
)
from lotuspond_sdk.executable._validation import report_bytes
from lotuspond_sdk.executable.framing import _signed_frame

SEED = '9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60'
PUBLIC = 'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a'
RFC_SIGNATURE = (
    'e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555f'
    'b8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b'
)
HERE = Path(__file__).resolve().parents[1] / 'fixtures/executable-v2'
ROOT = Path(__file__).resolve().parents[4]
ORACLES = json.loads((HERE / 'signing-v2.json').read_text())
KINDS = ('package', 'command')
SIGN = {'package': sign_executable_package, 'command': sign_runtime_command}
VERIFY = {'package': verify_executable_package, 'command': verify_runtime_command}
ADMIT = {'package': validate_executable_signature_envelope,
         'command': validate_command_signature_envelope}
MODEL = {'package': ExecutableSignatureEnvelope, 'command': CommandSignatureEnvelope}
SERIALIZE = {'package': executable_signature_envelope_json,
             'command': command_signature_envelope_json}


def key():
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(SEED))


def trust(state='allowed', private=None):
    public = (private or key()).public_key().public_bytes_raw()
    return {'format_version': 1, 'keys': [{
        'key_id': hashlib.sha256(public).hexdigest(), 'public_key': public.hex(), 'state': state,
    }]}


def oracle(kind):
    return copy.deepcopy(ORACLES[kind])


def independent_frame(envelope, canonical):
    header = {k: v for k, v in envelope.items() if k != 'signature'}
    h = json.dumps(header, sort_keys=True, ensure_ascii=True,
                   separators=(',', ':')).encode('ascii')
    return (envelope['context'].encode('ascii') + b'\0' + len(h).to_bytes(8, 'big') + h
            + len(canonical).to_bytes(8, 'big') + canonical)


@pytest.mark.parametrize('kind', KINDS)
def test_independent_literal_frame_and_signature(kind):
    literal = oracle(kind)
    envelope = SIGN[kind](literal['raw'], key())
    assert envelope.model_dump() == literal['envelope']
    canonical = bytes.fromhex(literal['canonical_hex'])
    frame = bytes.fromhex(literal['frame_hex'])
    assert independent_frame(literal['envelope'], canonical) == frame
    assert _signed_frame(ADMIT[kind](literal['envelope']), canonical) == frame
    key().public_key().verify(bytes.fromhex(literal['envelope']['signature']), frame)
    report = VERIFY[kind](literal['raw'], literal['envelope'], trust())
    assert json.loads(executable_verification_report_json(report)) == {
        'valid': True, 'code': 'verified',
        'content_kind': 'package_v2' if kind == 'package' else 'runtime_command_v1',
        'sha256': literal['envelope']['sha256'], 'key_id': literal['envelope']['key_id'],
        'contract_valid': True, 'compatibility_status': 'not_checked',
        'binding_status': 'not_checked', 'authority_granted': False,
    }
    assert 'public_key' not in envelope.model_dump()
    assert json.loads(SERIALIZE[kind](envelope)) == literal['envelope']


@pytest.mark.parametrize('kind', KINDS)
def test_openssl_both_directions_and_node_jcs(kind, tmp_path):
    assert shutil.which('openssl'), 'OpenSSL is a required Linux oracle prerequisite'
    assert shutil.which('node'), 'Node is a required JCS oracle prerequisite'
    literal = oracle(kind)
    canonical = subprocess.run(
        ['node', str(ROOT / 'scripts/checks/jcs-oracle.mjs')],
        input=json.dumps(literal['raw']).encode(), capture_output=True, check=True,
    ).stdout
    assert canonical.hex() == literal['canonical_hex']
    actual = (canonical_executable_json(literal['raw']) if kind == 'package'
              else canonical_wire_json(literal['raw']))
    assert actual == canonical
    # RFC 8410 section 10.3 DER wrapping of public RFC 8032 TEST 1 key material.
    (tmp_path / 'key.der').write_bytes(bytes.fromhex('302e020100300506032b657004220420' + SEED))
    (tmp_path / 'public.der').write_bytes(bytes.fromhex('302a300506032b6570032100' + PUBLIC))
    (tmp_path / 'frame').write_bytes(bytes.fromhex(literal['frame_hex']))
    subprocess.run([
        'openssl', 'pkeyutl', '-sign', '-rawin', '-keyform', 'DER', '-inkey',
        str(tmp_path / 'key.der'), '-in', str(tmp_path / 'frame'),
        '-out', str(tmp_path / 'signature'),
    ], check=True, capture_output=True)
    assert (tmp_path / 'signature').read_bytes().hex() == literal['envelope']['signature']
    assert VERIFY[kind](literal['raw'], literal['envelope'], trust()).valid
    (tmp_path / 'signature').write_bytes(bytes.fromhex(SIGN[kind](literal['raw'], key()).signature))
    subprocess.run([
        'openssl', 'pkeyutl', '-verify', '-rawin', '-pubin', '-keyform', 'DER', '-inkey',
        str(tmp_path / 'public.der'), '-in', str(tmp_path / 'frame'),
        '-sigfile', str(tmp_path / 'signature'),
    ], check=True, capture_output=True)


def test_rfc8032_key_attribution():
    assert key().public_key().public_bytes_raw().hex() == PUBLIC
    assert key().sign(b'').hex() == RFC_SIGNATURE


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('field,value', [
    ('format_version', True), ('format_version', 1.0), ('format_version', 3),
    ('algorithm', 'rsa'), ('context', 'lotuspond-evidence-manifest-v1'),
    ('schema_version', 'lotuspond.workflow-package.v1'),
    ('canonicalization', 'lotuspond.workflow-package.canonical-json.v1'),
    ('key_id', 'A' * 64), ('sha256', '0' * 63), ('signature', 'A' * 128),
    ('public_key', PUBLIC), ('unknown-private-value', 'must never be disclosed'),
])
def test_malformed_headers_raise_fixed_input_error(kind, field, value):
    literal = oracle(kind)
    envelope = literal['envelope']
    envelope[field] = value
    with pytest.raises(ExecutableError) as error:
        VERIFY[kind](literal['raw'], envelope, trust())
    assert error.value.code == 'invalid_envelope'
    assert 'must never be disclosed' not in str(error.value)


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('field', ['key_id', 'sha256', 'signature'])
def test_every_variable_header_field_is_bound(kind, field):
    literal = oracle(kind)
    other = Ed25519PrivateKey.from_private_bytes(b'\x42' * 32)
    trusted = trust()
    trusted['keys'] += trust(private=other)['keys']
    envelope = literal['envelope']
    if field == 'key_id':
        envelope[field] = trust(private=other)['keys'][0]['key_id']
        code = 'invalid_signature'
    elif field == 'sha256':
        envelope[field] = '0' * 64
        code = 'digest_mismatch'
    else:
        envelope[field] = '0' * 128
        code = 'invalid_signature'
    report = VERIFY[kind](literal['raw'], envelope, trusted)
    assert report.code == code
    assert not report.valid and report.authority_granted is False


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('state,code', [('unknown', 'untrusted_key'), ('revoked', 'revoked_key')])
def test_trust_refusal_precedes_wrong_digest_and_signature(kind, state, code):
    literal = oracle(kind)
    literal['envelope']['sha256'] = '0' * 64
    literal['envelope']['signature'] = '0' * 128
    trusted = {'format_version': 1, 'keys': []} if state == 'unknown' else trust('revoked')
    assert VERIFY[kind](literal['raw'], literal['envelope'], trusted).code == code


@pytest.mark.parametrize('kind,other_context', [
    (kind, context) for kind in KINDS for context in (
        'lotuspond-workflow-package-signature-v1', 'lotuspond-runtime-command-v1',
        'lotuspond-workflow-package-signature-v2', 'lotuspond-evidence-manifest-v1',
    ) if context != ORACLES[kind]['envelope']['context']
])
def test_signature_bytes_from_other_context_never_verify(kind, other_context):
    literal = oracle(kind)
    other = {**literal['envelope'], 'context': other_context}
    literal['envelope']['signature'] = key().sign(independent_frame(
        other, bytes.fromhex(literal['canonical_hex']))).hex()
    assert VERIFY[kind](literal['raw'], literal['envelope'], trust()).code == 'invalid_signature'
    with pytest.raises(ExecutableError) as error:
        VERIFY[kind](literal['raw'], other, trust())
    assert error.value.code == 'invalid_envelope'


def test_v1_and_v2_envelopes_do_not_fallback(example):
    old = sign_package(example, key())
    with pytest.raises(ExecutableError) as error:
        verify_executable_package(oracle('package')['raw'], old.model_dump(), trust())
    assert error.value.code == 'invalid_envelope'
    with pytest.raises(SdkError) as error:
        verify_package(example, oracle('package')['envelope'], trust())
    assert error.value.code == 'invalid_signature_envelope'
    for kind in KINDS:
        with pytest.raises(ExecutableError) as error:
            VERIFY[kind](oracle(kind)['raw'], oracle('command' if kind == 'package'
                                                    else 'package')['envelope'], trust())
        assert error.value.code == 'invalid_envelope'


@pytest.mark.parametrize('field', list(ORACLES['command']['raw']))
def test_complete_command_content_is_signed(field):
    literal = oracle('command')
    raw = literal['raw']
    if field == 'record_type' or field == 'format_version':
        raw[field] = 'action_intent' if field == 'record_type' else True
        with pytest.raises(ExecutableError):
            verify_runtime_command(raw, literal['envelope'], trust())
        return
    if field.endswith('sha256'):
        raw[field] = 'b' * 64
    elif type(raw[field]) is int:
        raw[field] += 1
    else:
        raw[field] += '-changed'
    report = verify_runtime_command(raw, literal['envelope'], trust())
    assert report.code == 'digest_mismatch' and not report.valid


@pytest.mark.parametrize('kind', KINDS)
def test_wrong_content_and_wrong_algorithm_key(kind):
    literal = oracle(kind)
    if kind == 'package':
        literal['raw']['name'] = 'Changed synthetic package'
    else:
        literal['raw']['worker_id'] = 'another-worker'
    assert VERIFY[kind](literal['raw'], literal['envelope'], trust()).code == 'digest_mismatch'
    for invalid in (object(), key().public_key(), generate_private_key(65537, 2048)):
        with pytest.raises(SdkError) as error:
            SIGN[kind](literal['raw'], invalid)
        assert error.value.code == 'invalid_signing_key'


@pytest.mark.parametrize('kind', KINDS)
def test_trust_is_revalidated_including_mutated_nested_keys(kind):
    literal = oracle(kind)
    duplicated = trust()
    duplicated['keys'] *= 2
    mutated = TrustSet.model_validate(trust())
    mutated.keys[0].state = 'self-trusted'
    constructed = TrustSet.model_construct(format_version=True, keys=[])
    for raw in (duplicated, mutated, constructed):
        with pytest.raises(SdkError) as error:
            VERIFY[kind](literal['raw'], literal['envelope'], raw)
        assert error.value.code == 'invalid_trust_set'


@pytest.mark.parametrize('kind', KINDS)
# Independent libsodium 1.0.18 encodings; no import of the SDK guard table.
@pytest.mark.parametrize('y', [
    '0000000000000000000000000000000000000000000000000000000000000000',
    '0100000000000000000000000000000000000000000000000000000000000000',
    '26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05',
    'c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a',
    'ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f',
    'edffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f',
    'eeffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f',
    'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f',
])
@pytest.mark.parametrize('sign_bit', [0, 128])
def test_reviewed_key_encoding_guard_through_new_verifiers(kind, y, sign_bit):
    public = y[:-2] + f'{int(y[-2:], 16) | sign_bit:02x}'
    literal = oracle(kind)
    bad_id = hashlib.sha256(bytes.fromhex(public)).hexdigest()
    literal['envelope']['key_id'] = bad_id
    trusted = {'format_version': 1, 'keys': [dict(key_id=bad_id, public_key=public, state='allowed')]}
    with pytest.raises(SdkError) as error:
        VERIFY[kind](literal['raw'], literal['envelope'], trusted)
    assert error.value.code == 'invalid_trust_set'


@pytest.mark.parametrize('kind', KINDS)
def test_constructed_and_mutated_envelopes_revalidate_without_serializers(kind):
    literal = oracle(kind)
    raw = MODEL[kind].model_construct(**{**literal['envelope'], 'format_version': 1.0})
    with pytest.raises(ExecutableError):
        VERIFY[kind](literal['raw'], raw, trust())
    envelope = ADMIT[kind](literal['envelope'])
    envelope.signature = 'private-value'
    with pytest.raises(ExecutableError) as error:
        SERIALIZE[kind](envelope)
    assert error.value.code == 'invalid_envelope'

    class Hook(MODEL[kind]):
        def model_dump(self, **kwargs):
            raise AssertionError('untrusted serializer invoked')

    with pytest.raises(ExecutableError):
        ADMIT[kind](Hook.model_construct(**literal['envelope']))


@pytest.mark.parametrize('field,value', [
    ('valid', 1), ('contract_valid', 1), ('authority_granted', 0),
    ('authority_granted', True), ('binding_status', 'checked'),
    ('compatibility_status', 'compatible'), ('code', 'digest_mismatch'),
])
@pytest.mark.parametrize('constructed', [True, False])
def test_verification_reports_cannot_manufacture_assurance(field, value, constructed):
    report = verify_executable_package(oracle('package')['raw'], oracle('package')['envelope'], trust())
    raw = report.model_dump()
    raw[field] = value
    if constructed:
        raw = ExecutableVerificationReport.model_construct(**raw)
    else:
        setattr(report, field, value)
        raw = report
    with pytest.raises(ExecutableError):
        validate_executable_verification_report(raw)
    with pytest.raises(ExecutableError):
        executable_verification_report_json(raw)


def test_reports_and_envelopes_have_complete_ascii_output_cap():
    for kind in KINDS:
        literal = oracle(kind)
        envelope = ADMIT[kind](literal['envelope'])
        encoded = SERIALIZE[kind](envelope)
        assert encoded.endswith(b'\n') and encoded.isascii()
        assert report_bytes(envelope, model_type=MODEL[kind], model_types=(MODEL[kind],),
                            code='invalid_envelope', max_bytes=len(encoded)) == encoded
        with pytest.raises(ExecutableError) as error:
            report_bytes(envelope, model_type=MODEL[kind], model_types=(MODEL[kind],),
                         code='invalid_envelope', max_bytes=len(encoded) - 1)
        assert error.value.code == 'report_too_large'
    report = verify_runtime_command(oracle('command')['raw'], oracle('command')['envelope'], trust())
    encoded = executable_verification_report_json(report)
    with pytest.raises(ExecutableError) as error:
        report_bytes(report, model_type=ExecutableVerificationReport,
                     model_types=(ExecutableVerificationReport,), code='invalid_envelope',
                     max_bytes=len(encoded) - 1)
    assert error.value.code == 'report_too_large'


def test_complete_admission_rejects_constructed_commands_and_other_wire(action_binding):
    for raw in (action_binding, build_action_intent(action_binding),
                validate_runtime_command(oracle('command')['raw']).model_copy(
                    update={'expires_at_ms': 0})):
        with pytest.raises(ExecutableError) as error:
            sign_runtime_command(raw, key())
        assert error.value.code == 'invalid_wire'
        with pytest.raises(ExecutableError):
            verify_runtime_command(raw, oracle('command')['envelope'], trust())


@pytest.mark.parametrize('mutation', ['worker', 'time', 'epoch', 'policy'])
def test_signed_wrong_context_command_confers_no_authority(
    runtime_command, action_binding, human_decision, binding_context, mutation,
):
    if mutation == 'worker':
        runtime_command['worker_id'] = 'another-worker'
    elif mutation == 'time':
        binding_context['now_ms'] = 8000
    elif mutation == 'epoch':
        binding_context['recovery_epoch'] += 1
    else:
        binding_context['policy_sha256'] = '0' * 64
    envelope = sign_runtime_command(runtime_command, key())
    cryptographic = verify_runtime_command(runtime_command, envelope, trust())
    assert cryptographic.valid and cryptographic.binding_status == 'not_checked'
    assert cryptographic.authority_granted is False
    bound = check_command_binding(runtime_command, build_action_intent(action_binding),
                                  human_decision, binding_context)
    assert not bound.valid and bound.authority_granted is False


def test_signing_revalidates_mutated_package(case):
    from lotuspond_sdk.executable import validate_executable_package

    package = validate_executable_package(case('typed_agent'))
    package.body.steps[0].input = None
    with pytest.raises(ExecutableError):
        sign_executable_package(package, key())


def test_bool_and_integer_literals_refuse_direct_envelope_construction():
    with pytest.raises(ValidationError):
        ExecutableSignatureEnvelope(**{**oracle('package')['envelope'], 'format_version': 2.0})
    with pytest.raises(ValidationError):
        CommandSignatureEnvelope(**{**oracle('command')['envelope'], 'format_version': True})


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('field', list(ORACLES['package']['envelope']))
def test_each_signed_header_field_is_required(kind, field):
    literal = oracle(kind)
    del literal['envelope'][field]
    with pytest.raises(ExecutableError) as error:
        VERIFY[kind](literal['raw'], literal['envelope'], trust())
    assert error.value.code == 'invalid_envelope'


@pytest.mark.parametrize('kind', KINDS)
def test_actual_evidence_and_v1_signature_bytes_do_not_verify(kind, example):
    literal = oracle(kind)
    signatures = [
        sign_package(example, key()).signature,
        key().sign(b'lotuspond-evidence-manifest-v1\n'
                   + bytes.fromhex(literal['canonical_hex'])).hex(),
    ]
    for signature in signatures:
        literal['envelope']['signature'] = signature
        assert VERIFY[kind](literal['raw'], literal['envelope'], trust()).code == 'invalid_signature'


@pytest.mark.parametrize('kind', KINDS)
def test_bounds_apply_before_signing_and_verification(kind):
    literal = oracle(kind)
    raw = literal['raw']
    raw['unknown'] = 'private' * (1048576 if kind == 'package' else 65536)
    for operation in (lambda: SIGN[kind](raw, key()),
                      lambda: VERIFY[kind](raw, literal['envelope'], trust())):
        with pytest.raises(ExecutableError) as error:
            operation()
        assert error.value.code == 'input_too_large'


def test_package_unicode_and_normalized_command_defaults():
    raw = oracle('package')['raw']
    raw['name'] = 'Synthetic snowman \u2603'
    envelope = sign_executable_package(raw, key())
    canonical = canonical_executable_json(raw)
    assert b'\xe2\x98\x83' in canonical
    key().public_key().verify(bytes.fromhex(envelope.signature),
                              independent_frame(envelope.model_dump(), canonical))
    raw = oracle('command')['raw']
    normalized = sign_runtime_command(raw, key())
    del raw['decision_sha256']
    del raw['format_version']
    assert sign_runtime_command(raw, key()) == normalized
    assert verify_runtime_command(raw, normalized, TrustSet.model_validate(trust())).valid


@pytest.mark.parametrize('kind', KINDS)
def test_one_canonical_entry_and_same_captured_bytes(kind, monkeypatch):
    from lotuspond_sdk.executable import signing

    name = 'canonical_executable_json' if kind == 'package' else 'canonical_wire_json'
    original = getattr(signing, name)
    captured = []

    def record(raw):
        result = original(raw)
        captured.append(result)
        return result

    monkeypatch.setattr(signing, name, record)
    literal = oracle(kind)
    envelope = SIGN[kind](literal['raw'], key())
    assert captured == [bytes.fromhex(literal['canonical_hex'])]
    captured.clear()
    assert VERIFY[kind](literal['raw'], envelope, trust()).valid
    assert captured == [bytes.fromhex(literal['canonical_hex'])]
