type AccountType = 'individual' | 'organization';
export function registrationPayload(
  input: Record<string, FormDataEntryValue>,
  type: AccountType,
  invitation?: string,
) {
  const { account_choice: _choice, workspace, ...fields } = input;
  return {
    ...fields,
    account_type: type,
    ...(type === 'organization' ? { workspace } : {}),
    ...(invitation ? { invitation_token: invitation } : {}),
  };
}

const key = 'lotuspond.organization-draft';
type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
function browserStore(): Store | undefined {
  try {
    return typeof window === 'undefined' ? undefined : window.sessionStorage;
  } catch {
    return undefined;
  }
}
export function saveOrganizationDraft(
  email: string,
  name: string,
  storage = browserStore(),
  now = Date.now(),
) {
  try {
    storage?.setItem(
      key,
      JSON.stringify({
        email: email.toLowerCase(),
        name: name.trim().slice(0, 160),
        expires: now + 3_600_000,
      }),
    );
  } catch {
    /* A draft is optional. */
  }
}
export function organizationDraft(
  email: string,
  storage = browserStore(),
  now = Date.now(),
): string {
  try {
    const item = JSON.parse(storage?.getItem(key) || 'null');
    return item &&
      item.email === email.toLowerCase() &&
      typeof item.name === 'string' &&
      item.name.length <= 160 &&
      item.expires > now &&
      item.expires <= now + 3_600_000
      ? item.name
      : '';
  } catch {
    return '';
  }
}
export function clearOrganizationDraft(storage = browserStore()) {
  try {
    storage?.removeItem(key);
  } catch {
    /* A draft is optional. */
  }
}
