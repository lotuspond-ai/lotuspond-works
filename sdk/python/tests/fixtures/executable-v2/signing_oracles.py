"""Reproduce synthetic literals independently: Node JCS + OpenSSL, no SDK imports.

RFC 8032 section 7.1 TEST 1 (Josefsson/Liusvaara, January 2017):
https://www.rfc-editor.org/rfc/rfc8032.html#section-7.1
The seed is public test material, never a real publisher credential.
"""

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
HERE = Path(__file__).parent
SEED = '9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60'
PUBLIC = 'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a'
KEY_ID = hashlib.sha256(bytes.fromhex(PUBLIC)).hexdigest()
PACKAGE = json.loads((ROOT / 'contracts/examples/executable-v2/minimal.json').read_text())
COMMAND = {
    'record_type': 'runtime_command', 'format_version': 1,
    'command_id': 'command-1', 'replay_id': 'replay-1',
    'intent_sha256': 'a' * 64, 'decision_sha256': None,
    'installation_id': 'installation-1', 'recovery_epoch': 2,
    'workspace_id': 'workspace-1', 'project_id': 'project-1',
    'environment_id': 'environment-1', 'execution_id': 'execution-1',
    'action_id': 'action-1', 'worker_id': 'worker-1', 'fencing_generation': 3,
    'issued_at_ms': 1800, 'expires_at_ms': 8000,
}


def run(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, **kwargs).stdout


def main():
    oracles = {}
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        key = work / 'rfc8032-test1.der'
        # RFC 8410 section 10.3 PKCS#8 DER prefix; append the published 32-byte seed.
        key.write_bytes(bytes.fromhex('302e020100300506032b657004220420' + SEED))
        for name, content, version, context, schema, canonicalization in (
            ('package', PACKAGE, 2, 'lotuspond-workflow-package-signature-v2',
             'lotuspond.workflow-package.v2', 'lotuspond.workflow-package.jcs.v2'),
            ('command', COMMAND, 1, 'lotuspond-runtime-command-v1',
             'lotuspond.runtime-command.v1', 'lotuspond.runtime-wire.jcs.v1'),
        ):
            canonical = run('node', str(ROOT / 'scripts/checks/jcs-oracle.mjs'),
                            input=json.dumps(content).encode())
            header = dict(format_version=version, algorithm='ed25519', context=context,
                          schema_version=schema, canonicalization=canonicalization,
                          key_id=KEY_ID, sha256=hashlib.sha256(canonical).hexdigest())
            h = json.dumps(header, sort_keys=True, ensure_ascii=True,
                           separators=(',', ':')).encode('ascii')
            frame = (context.encode('ascii') + b'\0' + len(h).to_bytes(8, 'big') + h
                     + len(canonical).to_bytes(8, 'big') + canonical)
            (work / 'frame.bin').write_bytes(frame)
            run('openssl', 'pkeyutl', '-sign', '-rawin', '-keyform', 'DER', '-inkey',
                str(key), '-in', str(work / 'frame.bin'), '-out', str(work / 'signature.bin'))
            signature = (work / 'signature.bin').read_bytes().hex()
            oracles[name] = dict(raw=content, canonical_hex=canonical.hex(), header_ascii=h.decode(),
                                 frame_hex=frame.hex(), envelope={**header, 'signature': signature})
    (HERE / 'signing-v2.json').write_text(json.dumps(oracles, indent=2) + '\n')
    print('Independent Node/OpenSSL package and command literal oracles written')


if __name__ == '__main__':
    main()
