export type IdentityRoute = 'verify-email' | 'reset-password' | 'accept-invitation';
const routes = new Set<IdentityRoute>(['verify-email', 'reset-password', 'accept-invitation']);
const invitationKey = 'lotuspond.pending-invitation';
const retentionMs = 60 * 60 * 1000;
type Storage = Pick<globalThis.Storage, 'getItem' | 'setItem' | 'removeItem'>;

export function isIdentityRoute(page: string): page is IdentityRoute {
  return routes.has(page as IdentityRoute);
}

export function parseIdentityLink(href: string) {
  const url = new URL(href);
  const [page = '', query = ''] = url.hash.slice(1).split('?');
  const parameters = new URLSearchParams(query);
  const values = parameters.getAll('token');
  const token =
    isIdentityRoute(page) && values.length === 1 && /^[A-Za-z0-9_-]{20,200}$/.test(values[0])
      ? values[0]
      : undefined;
  const supplied = parameters.has('token') || url.searchParams.has('token');
  // Remove even invalid or misplaced credentials before any application request.
  parameters.delete('token');
  url.searchParams.delete('token');
  url.hash = `${page}${parameters.size ? `?${parameters}` : ''}`;
  return { page: page || 'home', token, supplied, cleanHref: url.href };
}

export function createIdentityLinks(storage?: Storage, now = Date.now) {
  const tokens = new Map<IdentityRoute, { token: string; expires: number }>();
  const removeStored = () => {
    try {
      storage?.removeItem(invitationKey);
    } catch {
      /* Storage can be disabled. */
    }
  };
  try {
    const item = JSON.parse(storage?.getItem(invitationKey) || 'null');
    if (
      item &&
      typeof item.token === 'string' &&
      /^[A-Za-z0-9_-]{20,200}$/.test(item.token) &&
      typeof item.expires === 'number' &&
      item.expires > now() &&
      item.expires <= now() + retentionMs
    )
      tokens.set('accept-invitation', item);
    else removeStored();
  } catch {
    removeStored();
  }

  function clear(route: IdentityRoute) {
    tokens.delete(route);
    if (route === 'accept-invitation') removeStored();
  }
  return {
    capture(location: Pick<Location, 'href'>, history: Pick<History, 'replaceState' | 'state'>) {
      const link = parseIdentityLink(location.href);
      if (link.supplied) history.replaceState(history.state, '', link.cleanHref);
      if (isIdentityRoute(link.page) && link.supplied) {
        clear(link.page);
        if (link.token) {
          const item = { token: link.token, expires: now() + retentionMs };
          tokens.set(link.page, item);
          if (link.page === 'accept-invitation') {
            try {
              storage?.setItem(invitationKey, JSON.stringify(item));
            } catch {
              /* Keep in memory. */
            }
          }
        }
      }
      return link.page;
    },
    token(route: IdentityRoute) {
      const item = tokens.get(route);
      if (item && item.expires > now()) return item.token;
      clear(route);
      return undefined;
    },
    clear,
  };
}
export type IdentityLinks = ReturnType<typeof createIdentityLinks>;
