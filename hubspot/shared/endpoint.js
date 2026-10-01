// Shared plumbing for public endpoint functions (workflow actions and webhooks): the raw body, the signed URI and
// the response shape, in one place, because they are what differ between function runtimes.
import { client } from './hubspot-api.js';
import { header, verify } from './signature.js';

// HubSpot's app-function runtime puts a function's secrets in process.env (verified live on platform 2026.09;
// context carries accountId, body, headers, method and params). Its local dev runner passes context.secrets, so
// both are read. An empty value counts as missing, so a secret saved blank fails closed rather than verifying
// signatures against an empty key.
export function secret(context, name) {
  return context?.secrets?.[name] || process.env[name] || undefined;
}

// The HubSpot API as this app, with the token HubSpot gives the function.
export function appClient(context) {
  return client({ token: secret(context, 'PRIVATE_APP_ACCESS_TOKEN') });
}

export function rawBody(context) {
  const body = context.body;
  if (body === undefined || body === null) return '';
  return typeof body === 'string' ? body : JSON.stringify(body);
}

export function parsedBody(context) {
  const body = context.body;
  if (typeof body === 'string') return body ? JSON.parse(body) : {};
  return body ?? {};
}

// HubSpot signs the URL it called. A public app function is served at https://<portal domain>/hs/serverless/<path>;
// the runtime does not pass the domain in, so it comes from the ENDPOINT_BASE_URL secret
// (for example https://123.hs-sites-na2.com), which `python -m crm_platform bind` reads from the portal.
// Query parameters arrive as context.params on the app-function runtime (context.query on the local runner).
export function queryOf(context) {
  return context.query ?? context.params ?? {};
}

export function signedRequest(context, path, base = secret(context, 'ENDPOINT_BASE_URL')) {
  const query = new URLSearchParams(queryOf(context)).toString();
  return {
    method: context.method ?? 'POST',
    uri: `${(base ?? '').replace(/\/$/, '')}${path}${query ? `?${query}` : ''}`,
    body: rawBody(context),
    timestamp: header(context.headers, 'x-hubspot-request-timestamp'),
    signature: header(context.headers, 'x-hubspot-signature-v3'),
  };
}

export function authenticate(context, path, { clientSecret = secret(context, 'HUBSPOT_CLIENT_SECRET'), now = Date.now() } = {}) {
  return verify(clientSecret, signedRequest(context, path), now);
}

// Who made a hubspot.fetch request from a card. HubSpot appends userId, userEmail, portalId and appId to the URL
// and signs the URL, so after authenticate() succeeds these are HubSpot's word, not the browser's.
export function identity(context) {
  const q = queryOf(context);
  return { email: q.userEmail ? String(q.userEmail) : '', userId: q.userId ? String(q.userId) : '',
    portalId: q.portalId ? String(q.portalId) : '', appId: q.appId ? String(q.appId) : '' };
}

// Authenticate a card's signed request and name its user, or answer why not.
export function signedUser(context, path, options) {
  const auth = authenticate(context, path, options);
  if (!auth.ok) return { refused: respond(401, { ok: false, error: auth.reason }) };
  const who = identity(context);
  if (!who.email && !who.userId) {
    return { refused: respond(401, { ok: false, error: 'the signed request names no user' }) };
  }
  if (who.portalId && context.accountId && who.portalId !== String(context.accountId)) {
    return { refused: respond(403, { ok: false, error: 'the request was signed for another portal' }) };
  }
  return { who: who.email || `user ${who.userId}` };
}

// App functions answer { statusCode, body }; HubSpot serialises the body.
export function respond(statusCode, body) {
  return { statusCode, body };
}
