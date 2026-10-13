import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Account, Workspace } from '../api.js';
import { AuthenticatorPolicy } from '../components/AuthenticatorPolicy.js';
import {
  authenticatorPolicyView,
  authenticatorRequirement,
  type AuthenticatorPolicy as Policy,
} from './authenticatorPolicy.js';

const organization: Policy = {
  require_mfa: false,
  available: true,
  manageable: true,
  members_without_authenticator: 0,
};
const account: Account = {
  user: {
    id: 'synthetic',
    email: 'synthetic-policy@example.com',
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
const workspace: Workspace = {
  id: 'synthetic-workspace',
  name: 'Synthetic organization',
  kind: 'organization',
  role: 'owner',
  require_mfa: true,
};

test('an administrator without a factor is told to enroll instead of being offered the policy', () => {
  const view = authenticatorPolicyView(organization, { canEdit: true, enrolled: false });
  assert.equal(view.state, 'optional');
  assert.equal(view.action, null);
  assert.match(view.guidance, /Set up your own authenticator/);
});

test('enabling names the members who lose access, and withdrawing never threatens access', () => {
  const pending = { ...organization, members_without_authenticator: 2 };
  const enabling = authenticatorPolicyView(pending, { canEdit: true, enrolled: true });
  assert.equal(enabling.action, 'enable');
  assert.equal(enabling.actionLabel, 'Require an authenticator');
  assert.match(enabling.guidance, /2 members have no authenticator/);
  assert.match(enabling.guidance, /Keys created by those members stop working/);

  const single = authenticatorPolicyView(
    { ...pending, members_without_authenticator: 1 },
    { canEdit: true, enrolled: true },
  );
  assert.match(single.guidance, /1 member has no authenticator/);

  const covered = authenticatorPolicyView(organization, { canEdit: true, enrolled: true });
  assert.match(covered.guidance, /Every current member already holds an authenticator/);

  const withdrawing = authenticatorPolicyView(
    { ...organization, require_mfa: true },
    { canEdit: true, enrolled: true },
  );
  assert.equal(withdrawing.action, 'disable');
  assert.equal(withdrawing.state, 'required');
  assert.match(withdrawing.guidance, /keep their access/);
});

test('personal workspaces and unconfigured installations offer no control and claim nothing', () => {
  const personal = authenticatorPolicyView(
    { ...organization, manageable: false },
    { canEdit: true, enrolled: true },
  );
  assert.equal(personal.state, 'personal');
  assert.equal(personal.action, null);

  const unavailable = authenticatorPolicyView(
    { ...organization, available: false },
    { canEdit: true, enrolled: true },
  );
  assert.equal(unavailable.state, 'unavailable');
  assert.equal(unavailable.action, null);
  assert.match(unavailable.summary, /not configured/);

  const member = authenticatorPolicyView(organization, { canEdit: false, enrolled: true });
  assert.equal(member.action, null);
  assert.match(member.guidance, /owner or administrator/);
});

test('a member without a factor is warned only where a requirement actually applies', () => {
  assert.equal(authenticatorRequirement(account, workspace), 'organization');
  assert.equal(authenticatorRequirement(account, { ...workspace, require_mfa: false }), null);
  assert.equal(authenticatorRequirement(account, { ...workspace, kind: 'personal' }), null);
  assert.equal(
    authenticatorRequirement({ ...account, mfa: { ...account.mfa!, enabled: true } }, workspace),
    null,
  );
  assert.equal(authenticatorRequirement(account, undefined), null);
});

test('an installation requirement outranks the organization one and reaches every workspace', () => {
  const installation = {
    ...account,
    mfa: { ...account.mfa!, required_by_installation: true },
  };
  assert.equal(authenticatorRequirement(installation, workspace), 'installation');
  assert.equal(
    authenticatorRequirement(installation, { ...workspace, kind: 'personal', require_mfa: false }),
    'installation',
  );
  assert.equal(
    authenticatorRequirement(
      { ...installation, mfa: { ...installation.mfa, enabled: true } },
      workspace,
    ),
    null,
  );
  const view = authenticatorPolicyView(
    { ...organization, required_by_installation: true, members_without_authenticator: 3 },
    { canEdit: true, enrolled: true },
  );
  // The control stays available, but it must not promise an effect it cannot have.
  assert.equal(view.action, 'enable');
  assert.match(view.guidance, /installation already requires an authenticator everywhere/);
  assert.doesNotMatch(view.guidance, /3 members/);
});

test('the policy card states the current requirement without a control the viewer cannot use', () => {
  const html = renderToStaticMarkup(
    createElement(AuthenticatorPolicy, {
      workspace: 'synthetic-workspace',
      policy: { ...organization, require_mfa: true },
      canEdit: false,
      enrolled: true,
      onChanged: () => {},
    }),
  );
  assert.match(html, /Authenticator policy/);
  assert.match(html, /Every member and API key owner needs an enrolled authenticator/);
  assert.match(html, /owner or administrator can change this policy/);
  assert.doesNotMatch(html, /<button[^>]*>Stop requiring/);

  const editable = renderToStaticMarkup(
    createElement(AuthenticatorPolicy, {
      workspace: 'synthetic-workspace',
      policy: { ...organization, members_without_authenticator: 3 },
      canEdit: true,
      enrolled: true,
      onChanged: () => {},
    }),
  );
  assert.match(editable, /3 members have no authenticator/);
  assert.match(editable, /Require an authenticator<\/button>/);
});
