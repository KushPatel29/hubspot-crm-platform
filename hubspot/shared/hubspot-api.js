// The HubSpot API from inside an app function: the app's token, short retries, and errors that are safe to log.
// App functions time out after 15 seconds, so a retry waits at most two seconds and gives up after three tries;
// the caller (a card or a workflow) sees a clear failure rather than a timeout. Error messages carry HubSpot's
// category and correlation ID, never the response text (it can echo personal data) and never the token.

export class HubSpotApiError extends Error {
  constructor(status, method, path, category = '', correlationId = '') {
    super(`HubSpot ${method} ${path} failed: HTTP ${status}${category ? ` ${category}` : ''}` +
      `${correlationId ? ` (correlation ${correlationId})` : ''}`);
    this.status = status;
    this.category = category;
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function client({ token = process.env.PRIVATE_APP_ACCESS_TOKEN, fetchImpl = globalThis.fetch, wait = sleep,
  base = 'https://api.hubapi.com' } = {}) {
  if (!token) throw new Error('PRIVATE_APP_ACCESS_TOKEN is not available to this function');

  async function request(method, path, body) {
    let status = 0;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const response = await fetchImpl(base + path, {
        method,
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      status = response.status;
      if (status === 429 || status >= 500) {
        const retryAfter = Number(response.headers?.get?.('retry-after') ?? 0);
        await wait(Math.min(2000, Math.max(retryAfter * 1000, 250 * 2 ** attempt)));
        continue;
      }
      const text = await response.text();
      const payload = text ? JSON.parse(text) : {};
      if (status < 200 || status >= 300) {
        throw new HubSpotApiError(status, method, path.split('?')[0], payload.category, payload.correlationId);
      }
      return payload;
    }
    throw new HubSpotApiError(status, method, path.split('?')[0], 'still failing after retries');
  }

  const api = {
    request,
    get: (path) => request('GET', path),
    post: (path, body) => request('POST', path, body),
    patch: (path, body) => request('PATCH', path, body),
    put: (path, body) => request('PUT', path, body),

    // Associated record IDs (v4), following pages.
    async associated(fromType, id, toType) {
      const ids = [];
      let after = '';
      do {
        const page = await request('GET', `/crm/v4/objects/${fromType}/${id}/associations/${toType}?limit=500${after}`);
        for (const row of page.results ?? []) ids.push({ id: String(row.toObjectId), types: row.associationTypes ?? [] });
        const next = page.paging?.next?.after;
        after = next ? `&after=${next}` : '';
      } while (after);
      return ids;
    },

    async batchRead(objectType, ids, properties) {
      const out = [];
      for (let i = 0; i < ids.length; i += 100) {
        const page = await request('POST', `/crm/v3/objects/${objectType}/batch/read`, {
          inputs: ids.slice(i, i + 100).map((id) => ({ id })),
          properties,
        });
        out.push(...(page.results ?? []));
      }
      return out;
    },

    // A custom object's type ID from its name ("recommendation" -> "2-123456").
    async customType(name) {
      const schemas = await request('GET', '/crm/v3/schemas');
      const schema = (schemas.results ?? []).find((s) => s.name === name);
      if (!schema) throw new Error(`custom object "${name}" is not defined in this portal`);
      return schema.objectTypeId;
    },

    async labelType(fromType, toType, label) {
      const labels = await request('GET', `/crm/v4/associations/${fromType}/${toType}/labels`);
      const match = (labels.results ?? []).find((l) => (label ? l.label === label : !l.label));
      if (!match) throw new Error(`no association ${label ? `labelled "${label}"` : 'type'} from ${fromType} to ${toType}`);
      return { associationCategory: match.category, associationTypeId: match.typeId };
    },
  };
  return api;
}
