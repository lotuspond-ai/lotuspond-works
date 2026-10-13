import assert from 'node:assert/strict';
import test from 'node:test';
import { registrationPayload, organizationDraft, saveOrganizationDraft } from './registration.js';

test('verification-first signup preserves identity fields and invitation while omitting organization creation', () => {
  const fields = {
    name: 'Synthetic Person',
    email: 'synthetic@example.com',
    password: 'synthetic-password-only',
    account_choice: 'organization',
    workspace: 'Proposed organization',
  };
  const payload = registrationPayload(fields, 'individual', 'synthetic-invitation');
  assert.deepEqual(payload, {
    name: fields.name,
    email: fields.email,
    password: fields.password,
    account_type: 'individual',
    invitation_token: 'synthetic-invitation',
  });
  assert.equal(fields.workspace, 'Proposed organization');
  assert.equal(registrationPayload(fields, 'organization').workspace, 'Proposed organization');
});

test('organization draft is account-scoped, expires, and contains no registration password', () => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) || null,
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
    removeItem: (key: string) => {
      values.delete(key);
    },
  };
  saveOrganizationDraft('synthetic@example.com', 'Proposed organization', storage, 1000);
  assert.equal(organizationDraft('synthetic@example.com', storage, 2000), 'Proposed organization');
  assert.equal(organizationDraft('other@example.com', storage, 2000), '');
  assert.equal(organizationDraft('synthetic@example.com', storage, 3_601_001), '');
  assert.equal(
    [...values.values()].some((value) => value.includes('password')),
    false,
  );
});
