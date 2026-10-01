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
export function signedRequest(context, path, base = secret(context, 'ENDPOINT_BASE_URL')) {
  const query = new URLSearchParams(context.query ?? {}).toString();
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

// App functions answer { statusCode, body }; HubSpot serialises the body.
export function respond(statusCode, body) {
  return { statusCode, body };
}
