export class ApiUrlError extends Error {
  constructor() {
    super('Configure a permitted Lotuspond API URL.');
    this.name = 'ApiUrlError';
    this.code = 'invalid_configuration';
  }
}

function loopbackHostname(hostname) {
  return hostname === '127.0.0.1' || hostname === 'localhost' || hostname === '::1';
}

export function lotuspondApiBase(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new ApiUrlError();
  }
  if (
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    (url.protocol !== 'https:' && !(url.protocol === 'http:' && loopbackHostname(url.hostname)))
  )
    throw new ApiUrlError();
  let path = url.pathname.replace(/\/+$/, '');
  if (path === '') path = '/api/v1';
  else if (!path.endsWith('/api/v1')) path = `${path}/api/v1`;
  return `${url.origin}${path}`;
}
