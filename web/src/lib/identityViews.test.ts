import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Account } from '../api.js';
import { IdentityFlow, VerificationCard } from '../pages/IdentityFlow.js';
import { Invitations } from '../pages/Invitations.js';
import { createIdentityLinks } from './identityLinks.js';
import { identityLabel, identityVerified } from './identityStatus.js';

const account: Account = {
  user: {
    id: 'synthetic-user',
    email: 'synthetic@example.com',
    name: 'Synthetic',
    email_verified: false,
    is_platform_admin: false,
  },
  workspaces: [
    { id: 'synthetic-org', name: 'Synthetic Organization', role: 'admin', kind: 'organization' },
  ],
  capabilities: { mail_enabled: false, registration_enabled: true },
};
const refresh = async () => {};

test('unconfigured mail is explicit and cannot be presented as delivery', () => {
  const view = renderToStaticMarkup(
    createElement(VerificationCard, { account, onRefresh: refresh }),
  );
  assert.match(view, /Email delivery is disabled/);
  assert.match(view, /<button disabled="">Request verification email/);
  assert.doesNotMatch(view, /email sent|Email delivered|Your mailbox is verified/);
  const invitations = renderToStaticMarkup(
    createElement(Invitations, { account, workspace: account.workspaces[0] }),
  );
  assert.match(invitations, /Email delivery is disabled/);
  assert.match(invitations, /<button disabled="">Send invitation/);
});

test('invitation acceptance requires a verified account and never renders the token', () => {
  const links = createIdentityLinks();
  const token = 'synthetic_invitation_token_1234567890';
  links.capture(
    { href: `https://lotuspond.example/#accept-invitation?token=${token}` },
    { state: null, replaceState: () => {} },
  );
  const view = renderToStaticMarkup(
    createElement(IdentityFlow, {
      page: 'accept-invitation',
      links,
      account,
      onRefresh: refresh,
      onAccepted: refresh,
    }),
  );
  assert.match(view, /Identity verification/);
  assert.doesNotMatch(view, /<button[^>]*>Accept invitation/);
  assert.equal(view.includes(token), false);
});

test('missing recovery token cannot render a new-password submission form', () => {
  const view = renderToStaticMarkup(
    createElement(IdentityFlow, {
      page: 'reset-password',
      links: createIdentityLinks(),
      account: null,
      onRefresh: refresh,
      onAccepted: refresh,
    }),
  );
  assert.match(view, /Reopen the complete link/);
  assert.doesNotMatch(view, /name="new_password"/);
});

test('personal workspace and viewers cannot render invitation administration', () => {
  for (const workspace of [
    { ...account.workspaces[0], role: 'viewer' },
    { ...account.workspaces[0], kind: 'personal' as const },
  ]) {
    const view = renderToStaticMarkup(createElement(Invitations, { account, workspace }));
    assert.match(view, /Organization administrator access required/);
    assert.doesNotMatch(view, /<form/);
  }
});

test('operator attestation permits identity access without claiming mailbox ownership', () => {
  const attested: Account = {
    ...account,
    user: {
      ...account.user,
      identity_verified: true,
      identity_verification_source: 'operator_attested',
    },
  };
  assert.equal(identityVerified(attested.user), true);
  assert.equal(identityLabel(attested.user), 'Operator attested');
  const html = renderToStaticMarkup(
    createElement(VerificationCard, { account: attested, onRefresh: refresh }),
  );
  assert.match(html, /Operator attested/);
  assert.match(html, /Your mailbox has not been verified/);
  assert.doesNotMatch(html, /Your mailbox is verified/);
  const links = createIdentityLinks();
  links.capture(
    {
      href: 'https://lotuspond.example/#accept-invitation?token=synthetic_invitation_token_1234567890',
    },
    { state: null, replaceState: () => {} },
  );
  const invitation = renderToStaticMarkup(
    createElement(IdentityFlow, {
      page: 'accept-invitation',
      links,
      account: attested,
      onRefresh: refresh,
      onAccepted: refresh,
    }),
  );
  assert.match(invitation, /Email invitations require mailbox verification/);
  assert.doesNotMatch(invitation, /<button[^>]*>Accept invitation/);
});

test('isolated installation never suggests enabling prohibited mailbox verification', () => {
  const isolated: Account = {
    ...account,
    user: {
      ...account.user,
      identity_verified: true,
      identity_verification_source: 'operator_attested',
    },
    capabilities: {
      mail_enabled: false,
      registration_enabled: false,
      offline_enrollment_enabled: true,
    },
  };
  const html = renderToStaticMarkup(
    createElement(VerificationCard, { account: isolated, onRefresh: refresh }),
  );
  assert.match(html, /Mailbox verification is unavailable in this isolated installation/);
  assert.match(html, /Your operator manages account enrollment/);
  assert.doesNotMatch(html, /configure mail/);
  assert.match(html, /<button disabled="">Request verification email/);
  const invitations = renderToStaticMarkup(
    createElement(Invitations, { account: isolated, workspace: isolated.workspaces[0] }),
  );
  assert.match(invitations, /Email invitations are unavailable in this isolated installation/);
  assert.match(invitations, /add their account through Members/);
  assert.doesNotMatch(invitations, /configure mail/);
  assert.match(invitations, /<button disabled="">Send invitation/);
});
