// HubSpot request signature v3, for workflow-action and webhook requests arriving at an endpoint function.
// signature = base64(HMAC-SHA256(client secret, method + uri + body + timestamp)); requests older than five minutes
// are refused. HubSpot signs the URI with some characters decoded, so the URI is decoded the same way first (the
// same rule GrowthOps' webhook receiver uses, growthops/hubspot_webhooks.py).
import { createHmac, timingSafeEqual } from 'node:crypto';

export const MAX_AGE_MS = 5 * 60 * 1000;
const DECODE = {
  '%3A': ':', '%2F': '/', '%3F': '?', '%40': '@', '%21': '!', '%24': '$', '%27': "'",
  '%28': '(', '%29': ')', '%2A': '*', '%2C': ',', '%3B': ';',
};

export function canonicalUri(uri) {
  return uri.replace(/%3A|%2F|%3F|%40|%21|%24|%27|%28|%29|%2A|%2C|%3B/gi, (m) => DECODE[m.toUpperCase()]);
}

export function sign(secret, method, uri, body, timestamp) {
  return createHmac('sha256', secret)
    .update(`${method.toUpperCase()}${canonicalUri(uri)}${body}${timestamp}`)
    .digest('base64');
}

export function verify(secret, { method, uri, body, timestamp, signature }, now) {
  if (!secret) return { ok: false, reason: 'no client secret configured' };
  if (!signature || !timestamp) return { ok: false, reason: 'unsigned request' };
  const age = now - Number(timestamp);
  if (!Number.isFinite(age) || age > MAX_AGE_MS || age < -MAX_AGE_MS) return { ok: false, reason: 'stale timestamp' };
  const expected = Buffer.from(sign(secret, method, uri, body, timestamp));
  const given = Buffer.from(String(signature));
  if (expected.length !== given.length || !timingSafeEqual(expected, given)) {
    return { ok: false, reason: 'signature mismatch' };
  }
  return { ok: true };
}

// Header lookup that does not care about case (function runtimes differ).
export function header(headers, name) {
  const wanted = name.toLowerCase();
  for (const [key, value] of Object.entries(headers ?? {})) {
    if (key.toLowerCase() === wanted) return Array.isArray(value) ? value[0] : value;
  }
  return undefined;
}
