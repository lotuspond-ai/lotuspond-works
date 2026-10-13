import type { Account, Workspace } from '../api.js';

export type AuthenticatorPolicy = {
  require_mfa: boolean;
  available: boolean;
  manageable: boolean;
  required_by_installation?: boolean;
  members_without_authenticator?: number;
};

export type AuthenticatorPolicyView = {
  state: 'required' | 'optional' | 'personal' | 'unavailable';
  summary: string;
  action: 'enable' | 'disable' | null;
  actionLabel: string;
  guidance: string;
};

// The person changing the policy must already hold a factor, so it cannot lock out its author.
export function authenticatorPolicyView(
  policy: AuthenticatorPolicy,
  options: { canEdit: boolean; enrolled: boolean },
): AuthenticatorPolicyView {
  const without = policy.members_without_authenticator ?? 0;
  if (!policy.manageable)
    return {
      state: 'personal',
      summary: 'An authenticator requirement applies to organizations, not personal workspaces.',
      action: null,
      actionLabel: '',
      guidance: 'Your own authenticator is managed in Account security.',
    };
  if (!policy.available)
    return {
      state: 'unavailable',
      summary: 'Authenticator support is not configured on this installation.',
      action: null,
      actionLabel: '',
      guidance: 'An operator must configure the authenticator encryption key before this policy.',
    };
  const required = policy.require_mfa;
  const summary = required
    ? 'Every member and API key owner needs an enrolled authenticator to reach this organization.'
    : 'Members can reach this organization with a password-only account.';
  if (!options.canEdit)
    return {
      state: required ? 'required' : 'optional',
      summary,
      action: null,
      actionLabel: '',
      guidance: 'A workspace owner or administrator can change this policy.',
    };
  if (!options.enrolled)
    return {
      state: required ? 'required' : 'optional',
      summary,
      action: null,
      actionLabel: '',
      guidance: 'Set up your own authenticator in Account security before changing this policy.',
    };
  const guidance = policy.required_by_installation
    ? 'This installation already requires an authenticator everywhere, so changing this policy will not change who can reach the organization today.'
    : required
      ? 'Members keep their access while the requirement is withdrawn.'
      : without === 0
        ? 'Every current member already holds an authenticator.'
        : `${without} ${without === 1 ? 'member has' : 'members have'} no authenticator and will lose access to this organization until they enroll. Keys created by those members stop working too.`;
  return {
    state: required ? 'required' : 'optional',
    summary,
    action: required ? 'disable' : 'enable',
    actionLabel: required ? 'Stop requiring an authenticator' : 'Require an authenticator',
    guidance,
  };
}

// Which requirement is keeping this person out, if any. The installation wins because
// withdrawing the organization policy would not restore their access.
export function authenticatorRequirement(
  account: Account,
  workspace: Workspace | undefined,
): 'installation' | 'organization' | null {
  if (account.mfa?.enabled) return null;
  if (account.mfa?.required_by_installation) return 'installation';
  if (workspace?.kind === 'organization' && workspace.require_mfa) return 'organization';
  return null;
}
