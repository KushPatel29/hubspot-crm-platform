// Shared plumbing for public endpoint functions (workflow actions and webhooks): the raw body, the signed URI and
// the response shape, in one place, because they are what differ between function runtimes.
import { header, verify } from './signature.js';

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

// HubSpot signs the URL it called. The function is reached through the portal's domain, which the runtime does
// not pass in, so it comes from the ENDPOINT_BASE_URL secret (for example https://123.hs-sites-na2.com).
export function signedRequest(context, path, base = process.env.ENDPOINT_BASE_URL) {
  const query = new URLSearchParams(context.query ?? {}).toString();
  return {
    method: context.method ?? 'POST',
    uri: `${(base ?? '').replace(/\/$/, '')}${path}${query ? `?${query}` : ''}`,
    body: rawBody(context),
    timestamp: header(context.headers, 'x-hubspot-request-timestamp'),
    signature: header(context.headers, 'x-hubspot-signature-v3'),
  };
}

export function authenticate(context, path, { secret = process.env.HUBSPOT_CLIENT_SECRET, now = Date.now() } = {}) {
  return verify(secret, signedRequest(context, path), now);
}

export function respond(statusCode, body) {
  return { statusCode, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
}
