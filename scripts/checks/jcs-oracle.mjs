// Independent ECMAScript oracle. Test-only; imports no Python or JCS implementation.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

function validString(value) {
  for (let i = 0; i < value.length; i++) {
    const unit = value.charCodeAt(i);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(++i);
      if (!(next >= 0xdc00 && next <= 0xdfff)) throw new Error('invalid_unicode');
    } else if (unit >= 0xdc00 && unit <= 0xdfff) throw new Error('invalid_unicode');
  }
}

function canonical(value) {
  if (typeof value === 'string') validString(value);
  if (typeof value === 'number' && !Number.isFinite(value)) throw new Error('nonfinite_number');
  if (value === null || ['boolean', 'string', 'number'].includes(typeof value)) {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (typeof value === 'object') {
    // Assemble members directly: JSON.stringify(object) reorders integer-like names.
    return '{' + Object.keys(value).sort().map(key => {
      validString(key);
      return JSON.stringify(key) + ':' + canonical(value[key]);
    }).join(',') + '}';
  }
  throw new Error('invalid_json_value');
}

if (process.argv[2] === '--verify') {
  const vectors = JSON.parse(readFileSync(process.argv[3], 'utf8'));
  for (const [hex, expected] of vectors) {
    const number = Buffer.from(hex, 'hex').readDoubleBE();
    if (expected === null) assert.throws(() => canonical(number));
    else assert.equal(canonical(number), expected);
  }
  assert.equal(canonical({ '2': 2, '10': 10 }), '{"10":10,"2":2}');
  assert.equal(canonical({ '\ue000': 2, '\ud800\udc00': 1 }), '{"𐀀":1,"":2}');
  assert.throws(() => canonical('\ud800'));
  console.log(`${vectors.length} RFC 8785 numeric vectors and ordering/Unicode checks passed`);
} else {
  process.stdout.write(canonical(JSON.parse(readFileSync(0, 'utf8'))));
}
