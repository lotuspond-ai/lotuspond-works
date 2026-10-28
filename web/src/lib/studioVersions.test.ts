import assert from 'node:assert/strict';
import test from 'node:test';
import { compareVersions, nextFreeVersion, publication, sameContent } from './studioVersions.js';

const listed = (version: string, sha256: string, packageId = 'intake') => ({
  package_id: packageId,
  version,
  sha256,
});

test('versions compare numerically, highest first, with unparsable ones last', () => {
  const sorted = ['1.2.0', '1.10.0', '0.9.9', 'draft', '1.2.10', '1.2.9'].sort(compareVersions);
  assert.deepEqual(sorted, ['1.10.0', '1.2.10', '1.2.9', '1.2.0', '0.9.9', 'draft']);
});

test('the suggested version is the next patch after every published one', () => {
  assert.equal(nextFreeVersion(['1.0.0', '1.0.1'], '1.0.0'), '1.0.2');
  assert.equal(nextFreeVersion(['1.0.0'], '2.3.4'), '2.3.5');
  assert.equal(nextFreeVersion([], 'not a version'), '0.0.1');
  assert.equal(nextFreeVersion(['1.4.999999'], '1.4.0'), '1.5.0');
  assert.equal(nextFreeVersion(['3.999999.999999'], '1.0.0'), '4.0.0');
});

test('the registry answer is foreseen only from a known digest', () => {
  const versions = [
    listed('1.0.0', 'aaa'),
    listed('1.0.1', 'bbb'),
    listed('1.0.0', 'ccc', 'other'),
  ];
  const pkg = { package_id: 'intake', version: '1.0.0' };
  assert.deepEqual(publication(versions, pkg, ''), { state: 'unknown' });
  assert.deepEqual(publication(versions, pkg, 'aaa'), { state: 'same' });
  assert.deepEqual(publication(versions, pkg, 'zzz'), { state: 'conflict', suggestion: '1.0.2' });
  assert.deepEqual(publication(versions, { ...pkg, version: '1.1.0' }, 'zzz'), { state: 'new' });
  // Another package's versions never count against this one.
  assert.deepEqual(publication(versions, { package_id: 'fresh', version: '1.0.0' }, 'ccc'), {
    state: 'new',
  });
});

test('a published version has the same content as a draft only by digest', () => {
  assert.equal(sameContent(listed('1.0.0', 'aaa'), 'aaa'), true);
  assert.equal(sameContent(listed('1.0.0', 'aaa'), ''), false);
  assert.equal(sameContent(listed('1.0.0', ''), ''), false);
});
