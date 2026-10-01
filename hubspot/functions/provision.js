// The write path of the CRM platform, running inside HubSpot under the app's own token.
//
// A developer's personal access key cannot write standard CRM records or their schemas, and HubSpot will not mint
// local-dev app tokens on test accounts. Rather than copy an app token out of HubSpot, the platform's loader
// (python -m crm_platform apply) sends each API call here, signed, and this function makes it with the token
// HubSpot gives the app, so no HubSpot credential ever leaves HubSpot.
//
// Guards, in order:
//   * Signature: hex HMAC-SHA256 over "<timestamp>.<method>.<path>.<bodyText>" with PROVISION_KEY, a random key the
//     loader generated for this portal and stored as an app secret. Timestamps older than five minutes are refused.
//   * Allowlist: only the calls the loader makes (account details; properties, groups, schemas, pipelines and
//     association labels; object list, batch read/create/update; association batch read/create). Never DELETE, and
//     nothing outside the CRM.
//   * The upstream status, body and rate-limit headers are returned as they came, so the loader's own retry,
//     pacing and 207 handling work unchanged.
import { createHmac, timingSafeEqual } from 'node:crypto';
import { header } from '../shared/signature.js';
import { parsedBody, respond, secret } from '../shared/endpoint.js';

export const PATH = '/hs/serverless/provision';
export const MAX_AGE_MS = 5 * 60 * 1000;
const T = '[0-9]+-[0-9]+|[a-z_]+';
export const ALLOWED = [
  ['GET', new RegExp(`^/account-info/v3/details$`)],
  ['GET', new RegExp(`^/crm/v3/schemas$`)],
  ['POST', new RegExp(`^/crm/v3/schemas$`)],
  ['POST', new RegExp(`^/crm/v3/schemas/(${T})/associations$`)],
  ['GET', new RegExp(`^/crm/v3/properties/(${T})(/groups)?$`)],
  ['POST', new RegExp(`^/crm/v3/properties/(${T})/(groups|batch/create)$`)],
  ['PATCH', new RegExp(`^/crm/v3/properties/(${T})/[a-z0-9_]+$`)],
  ['GET', new RegExp(`^/crm/v3/pipelines/(${T})$`)],
  ['POST', new RegExp(`^/crm/v3/pipelines/(${T})(/[A-Za-z0-9_-]+/stages)?$`)],
  ['PATCH', new RegExp(`^/crm/v3/pipelines/(${T})/[A-Za-z0-9_-]+/stages/[A-Za-z0-9_-]+$`)],
  ['GET', new RegExp(`^/crm/v4/associations/(${T})/(${T})/labels$`)],
  ['POST', new RegExp(`^/crm/v4/associations/(${T})/(${T})/(labels|batch/read|batch/create)$`)],
  ['GET', new RegExp(`^/crm/v3/objects/(${T})$`)],
  ['POST', new RegExp(`^/crm/v3/objects/(${T})/batch/(read|create|update)$`)],
];

export function allowed(method, path) {
  const route = path.split('?')[0];
  return ALLOWED.some(([m, pattern]) => m === method && pattern.test(route));
}

export function signRequest(key, timestamp, method, path, body) {
  return createHmac('sha256', key).update(`${timestamp}.${method}.${path}.${body}`).digest('hex');
}

function verified(key, context, request, now) {
  const timestamp = Number(header(context.headers, 'x-crm-platform-timestamp'));
  const given = String(header(context.headers, 'x-crm-platform-signature') ?? '');
  if (!key) return 'no provision key configured';
  if (!Number.isFinite(timestamp) || Math.abs(now - timestamp) > MAX_AGE_MS) return 'stale or missing timestamp';
  // The body is signed and forwarded as the exact text the loader sent, so no JSON re-serialisation can differ.
  const expected = signRequest(key, timestamp, request.method, request.path, request.bodyText ?? '');
  const a = Buffer.from(expected);
  const b = Buffer.from(given);
  return a.length === b.length && timingSafeEqual(a, b) ? null : 'signature mismatch';
}

export async function main(context, { now = Date.now(), fetchImpl = globalThis.fetch, key, token } = {}) {
  const request = parsedBody(context);
  const problem = verified(key ?? secret(context, 'PROVISION_KEY'), context, request, now);
  if (problem) return respond(401, { error: problem });
  if (!allowed(request.method, request.path ?? '')) {
    return respond(403, { error: `not allowed: ${request.method} ${String(request.path).split('?')[0]}` });
  }
  const appToken = token ?? secret(context, 'PRIVATE_APP_ACCESS_TOKEN');
  const upstream = await fetchImpl(`https://api.hubapi.com${request.path}`, {
    method: request.method,
    headers: { Authorization: `Bearer ${appToken}`, 'Content-Type': 'application/json' },
    body: request.bodyText ? request.bodyText : undefined,
  });
  const text = await upstream.text();
  const headers = {};
  for (const name of ['retry-after', 'x-hubspot-ratelimit-daily-remaining', 'x-hubspot-ratelimit-remaining']) {
    const value = upstream.headers?.get?.(name);
    if (value) headers[name] = value;
  }
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  return respond(200, { status: upstream.status, headers, body });
}
