import assert from 'node:assert/strict';
import test from 'node:test';
import { ApiUrlError, lotuspondApiBase } from './api-url.mjs';

test('origin-only and /api/v1 API URLs normalize to the same base', () => {
  assert.equal(lotuspondApiBase('http://127.0.0.1:8000'), 'http://127.0.0.1:8000/api/v1');
  assert.equal(lotuspondApiBase('http://127.0.0.1:8000/'), 'http://127.0.0.1:8000/api/v1');
  assert.equal(lotuspondApiBase('http://127.0.0.1:8000/api/v1'), 'http://127.0.0.1:8000/api/v1');
  assert.equal(lotuspondApiBase('http://127.0.0.1:8000/api/v1/'), 'http://127.0.0.1:8000/api/v1');
  assert.equal(
    lotuspondApiBase('https://lotuspond.example.com'),
    'https://lotuspond.example.com/api/v1',
  );
  assert.equal(
    lotuspondApiBase('https://lotuspond.example.com/api/v1'),
    'https://lotuspond.example.com/api/v1',
  );
  assert.equal(
    lotuspondApiBase('https://lotuspond.example.com/prefix/api/v1'),
    'https://lotuspond.example.com/prefix/api/v1',
  );
  assert.equal(
    lotuspondApiBase('https://lotuspond.example.com/prefix'),
    'https://lotuspond.example.com/prefix/api/v1',
  );
});

test('API URLs reject credentials, fragments, remote plaintext, and invalid values', () => {
  for (const value of [
    'http://example.com',
    'https://user:secret@example.test',
    'https://example.test?token=secret',
    'https://example.test/#fragment',
    'not-a-url',
  ])
    assert.throws(() => lotuspondApiBase(value), ApiUrlError);
});
