import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Account } from '../api.js';
import { Authenticator, RecoveryCodes } from '../components/Authenticator.js';
import { MfaChallengeForm } from '../components/MfaChallenge.js';
import { loginChallenge, privilegedMfaNeeded, type MfaChallenge } from './mfa.js';

const account: Account = {
  user: {
    id: 'synthetic',
    email: 'synthetic-mfa@example.com',
    name: 'Synthetic',
    is_platform_admin: false,
    email_verified: true,
  },
  workspaces: [],
  mfa: {
    available: true,
    enabled: false,
    session_verified: false,
    recovery_codes_remaining: 0,
    required_for_privileged: false,
  },
};
const refresh = async () => {};
const challenge: MfaChallenge = {
  mfa_required: true,
  challenge_token: 'synthetic-challenge-not-a-real-secret',
  expires_at: '2026-09-21T20:00:00Z',
};

test('password acceptance with MFA always remains a challenge, including malformed or expired replies', () => {
  const now = Date.parse('2026-09-21T19:59:00Z');
  assert.equal(loginChallenge(challenge, now), challenge);
  assert.equal(loginChallenge({ workspaceId: 'synthetic-workspace' }, now), null);
  for (const invalid of [
    { ...challenge, challenge_token: '' },
    { ...challenge, expires_at: 'invalid' },
    { ...challenge, expires_at: '2026-09-21T19:58:00Z' },
  ])
    assert.throws(() => loginChallenge(invalid, now), /challenge is unavailable/);
});

test('challenge form never places the challenge token in rendered fields or links', () => {
  const html = renderToStaticMarkup(
    createElement(MfaChallengeForm, { challenge, onComplete: refresh, onCancel: () => {} }),
  );
  assert.match(html, /Your password was accepted/);
  assert.match(html, /Authenticator or recovery code/);
  assert.match(html, /autoComplete="one-time-code"/);
  assert.match(html, /Cancel sign-in/);
  assert.doesNotMatch(html, /name="password"|type="hidden"/);
  assert.equal(html.includes(challenge.challenge_token), false);
});

test('one-time recovery codes require an explicit saved acknowledgement before clearing', () => {
  const html = renderToStaticMarkup(
    createElement(RecoveryCodes, {
      codes: ['synthetic-code-one', 'synthetic-code-two'],
      onSaved: () => {},
    }),
  );
  assert.match(html, /These codes will not be shown again/);
  assert.match(html, /Leaving this page clears this display/);
  assert.match(html, /type="checkbox"/);
  assert.match(html, /<button disabled="">Done/);
  assert.doesNotMatch(html, /href=|src=|type="hidden"/);
});

test('unavailable authenticator setup is disabled and does not claim it is enabled', () => {
  const html = renderToStaticMarkup(
    createElement(Authenticator, {
      account: { ...account, mfa: { ...account.mfa!, available: false } },
      onChanged: refresh,
      onSignedOut: refresh,
    }),
  );
  assert.match(html, /unavailable on this installation/);
  assert.match(html, /<button disabled="">Set up authenticator/);
  assert.doesNotMatch(html, /name="current_password"|name="code"/);
});

test('required privileged MFA directs setup or verified sign-in and cannot offer disable', () => {
  const admin = {
    ...account,
    user: { ...account.user, is_platform_admin: true },
    mfa: { ...account.mfa!, required_for_privileged: true },
  };
  assert.equal(privilegedMfaNeeded(admin), true);
  assert.equal(
    privilegedMfaNeeded({ ...admin, mfa: { ...admin.mfa, enabled: true, session_verified: true } }),
    false,
  );
  assert.equal(privilegedMfaNeeded(account), false);
  const html = renderToStaticMarkup(
    createElement(Authenticator, {
      account: { ...admin, mfa: { ...admin.mfa, enabled: true, recovery_codes_remaining: 5 } },
      onChanged: refresh,
      onSignedOut: refresh,
    }),
  );
  assert.match(html, /Platform administration requires/);
  assert.match(html, /Sign out to verify/);
  assert.match(html, /Replace recovery codes/);
  assert.doesNotMatch(html, /Disable authenticator/);
});
