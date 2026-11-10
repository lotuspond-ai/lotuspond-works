export type Row = Record<string, unknown> & { id: string };
export type Workspace = {
  id: string;
  name: string;
  kind: 'personal' | 'organization';
  role: string;
};
export type Account = {
  user: {
    id: string;
    email: string;
    name: string;
    is_platform_admin: boolean;
    email_verified: boolean;
    identity_verified?: boolean;
    identity_verification_source?: 'mailbox' | null;
  };
  workspaces: Workspace[];
  mfa?: {
    available: boolean;
    enabled: boolean;
    session_verified: boolean;
    recovery_codes_remaining: number;
    required_for_privileged: boolean;
  };
  capabilities?: {
    mail_enabled: boolean;
    registration_enabled: boolean;
  };
};
// A field-level problem the server reports without echoing the submitted value.
export type FieldProblem = { path: (string | number)[]; code: string; message: string };
export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code?: string,
    public readonly problems: FieldProblem[] = [],
  ) {
    super(message);
  }
}
function fieldProblems(value: unknown): FieldProblem[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) =>
    item &&
    typeof item === 'object' &&
    Array.isArray(item.path) &&
    typeof item.code === 'string' &&
    typeof item.message === 'string'
      ? [{ path: item.path, code: item.code, message: item.message }]
      : [],
  );
}
export async function api<T>(
  path: string,
  workspace?: string,
  body?: unknown,
  method = body === undefined ? 'GET' : 'POST',
  signal?: AbortSignal,
  headers: Record<string, string> = {},
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method,
    signal,
    credentials: 'same-origin',
    headers: {
      ...(body !== undefined ? { 'content-type': 'application/json' } : {}),
      ...(workspace ? { 'x-lotuspond-workspace': workspace } : {}),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const message =
      typeof data?.message === 'string'
        ? data.message
        : typeof data?.detail === 'string'
          ? data.detail
          : response.status === 401
            ? 'Sign in to continue.'
            : response.status === 403
              ? 'You do not have permission to perform this action.'
              : 'The request could not be completed. Please try again.';
    throw new ApiError(
      message,
      response.status,
      typeof data?.error === 'string' ? data.error : undefined,
      fieldProblems(data?.errors),
    );
  }
  return data as T;
}
