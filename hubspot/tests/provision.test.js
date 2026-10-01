import { describe, expect, it } from 'vitest';
import { allowed, main, signRequest } from '../functions/provision.js';

const KEY = 'k'.repeat(64);
const NOW = Date.parse('2026-10-01T12:00:00Z');

function call(method, path, body, { key = KEY, at = NOW } = {}) {
  const bodyText = body === undefined ? '' : JSON.stringify(body);
  const signature = signRequest(key, at, method, path, bodyText);
  return { method: 'POST', params: {}, headers: {},
    body: JSON.stringify({ method, path, bodyText, timestamp: String(at), signature }) };
}

function upstream(status, body, headers = {}) {
  const seen = [];
  const fetchImpl = async (url, options) => {
    seen.push({ url, ...options });
    return { status, text: async () => JSON.stringify(body), headers: { get: (n) => headers[n] } };
  };
  return { fetchImpl, seen };
}

describe('provision function', () => {
  it("forwards a signed, allowed call with the app token and returns HubSpot's answer as it came", async () => {
    const { fetchImpl, seen } = upstream(207, { results: [], errors: [{ category: 'VALIDATION_ERROR' }] },
      { 'retry-after': '1' });
    const body = { inputs: [{ properties: { name: 'Acme' } }] };
    const response = await main(call('POST', '/crm/v3/objects/0-2/batch/create', body),
      { now: NOW, fetchImpl, key: KEY, token: 'app-token' });
    expect(response.statusCode).toBe(200);
    expect(response.body).toMatchObject({ status: 207, headers: { 'retry-after': '1' } });
    expect(seen[0].url).toBe('https://api.hubapi.com/crm/v3/objects/0-2/batch/create');
    expect(seen[0].headers.Authorization).toBe('Bearer app-token');
    expect(seen[0].body).toBe(JSON.stringify(body));
  });

  it('refuses forged, replayed and unkeyed calls before calling HubSpot', async () => {
    const { fetchImpl, seen } = upstream(200, {});
    const forged = call('POST', '/crm/v3/objects/0-2/batch/create', { inputs: [] }, { key: 'x'.repeat(64) });
    expect((await main(forged, { now: NOW, fetchImpl, key: KEY })).body.error).toBe('signature mismatch');
    const replayed = call('GET', '/crm/v3/schemas', undefined, { at: NOW - 6 * 60 * 1000 });
    expect((await main(replayed, { now: NOW, fetchImpl, key: KEY })).body.error).toBe('stale or missing timestamp');
    expect((await main(call('GET', '/crm/v3/schemas'), { now: NOW, fetchImpl, key: '' })).statusCode).toBe(401);
    expect(seen).toHaveLength(0);
  });

  it("allows only the loader's calls: never DELETE, nothing outside the CRM", async () => {
    expect(allowed('POST', '/crm/v3/properties/0-2/batch/create')).toBe(true);
    expect(allowed('GET', '/crm/v3/objects/2-123?limit=100&properties=a')).toBe(true);
    expect(allowed('POST', '/crm/v4/associations/0-1/0-2/batch/create')).toBe(true);
    expect(allowed('DELETE', '/crm/v3/objects/0-1/1')).toBe(false);
    expect(allowed('POST', '/crm/v3/objects/0-1/batch/archive')).toBe(false);
    expect(allowed('GET', '/settings/v3/users')).toBe(false);
    expect(allowed('POST', '/cms/v3/functions/secrets')).toBe(false);
    const { fetchImpl, seen } = upstream(200, {});
    const response = await main(call('DELETE', '/crm/v3/objects/0-1/1'), { now: NOW, fetchImpl, key: KEY });
    expect(response.statusCode).toBe(403);
    expect(seen).toHaveLength(0);
  });
});
