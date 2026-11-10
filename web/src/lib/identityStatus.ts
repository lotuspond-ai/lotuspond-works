import type { Account } from '../api.js';

export function identityVerified(user: Account['user']) {
  return user.identity_verified ?? user.email_verified;
}

export function identityLabel(user: Account['user']) {
  if (user.email_verified) return 'Mailbox verified';
  return identityVerified(user) ? 'Identity verified · source not recorded' : 'Unverified';
}
