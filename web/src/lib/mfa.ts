import type { Account } from '../api.js';

export type MfaChallenge = { mfa_required: true; challenge_token: string; expires_at: string };
export type LoginResult = { workspaceId?: string; mfa_required?: false } | MfaChallenge;

// A password response that requests MFA must never be mistaken for an authenticated session.
export function loginChallenge(result: LoginResult, now = Date.now()): MfaChallenge | null {
  if (result.mfa_required !== true) return null;
  if (
    !result.challenge_token ||
    !Number.isFinite(Date.parse(result.expires_at)) ||
    Date.parse(result.expires_at) <= now
  )
    throw new Error('This sign-in challenge is unavailable. Sign in again to request a new one.');
  return result;
}

export function privilegedMfaNeeded(account: Account): boolean {
  return !!(
    account.user.is_platform_admin &&
    account.mfa?.required_for_privileged &&
    !account.mfa.session_verified
  );
}
