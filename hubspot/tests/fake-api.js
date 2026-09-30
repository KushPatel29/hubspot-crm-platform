// An in-memory stand-in for the `api` object the functions receive (shared/hubspot-api.js), with the behaviour the
// functions rely on: associations with labels, batch reads, custom object types, pipelines, and idempotent PUTs.
export function fakeApi({ schemas = {}, pipelines = {}, labels = {} } = {}) {
  let next = 100;
  const objects = {};
  const links = [];
  const calls = [];
  const state = { objects, links, calls, failNext: null };

  const record = (type, id) => objects[type]?.[id];
  const add = (type, properties, id = String(++next)) => {
    objects[type] ??= {};
    objects[type][id] = { id, properties: { ...properties } };
    return objects[type][id];
  };
  const link = (fromType, fromId, toType, toId, label = null) => {
    const key = (l) => `${l.fromType}|${l.fromId}|${l.toType}|${l.toId}|${l.label}`;
    const item = { fromType, fromId: String(fromId), toType, toId: String(toId), label };
    if (!links.some((l) => key(l) === key(item))) links.push(item);
  };
  const guard = (name) => {
    calls.push(name);
    if (state.failNext === name) {
      state.failNext = null;
      throw new Error(`injected failure in ${name}`);
    }
  };

  const api = {
    async associated(fromType, id, toType) {
      guard(`associated ${fromType}->${toType}`);
      const seen = new Map();
      for (const l of links) {
        if (l.fromType === fromType && l.fromId === String(id) && l.toType === toType) seen.set(l.toId, l);
        if (l.toType === fromType && l.toId === String(id) && l.fromType === toType) seen.set(l.fromId, l);
      }
      return [...seen.keys()].map((toId) => ({ id: toId, types: [] }));
    },
    async batchRead(type, ids, properties) {
      guard(`batchRead ${type}`);
      return ids.map((id) => record(type, id)).filter(Boolean).map((r) => ({
        id: r.id, properties: Object.fromEntries(properties.map((p) => [p, r.properties[p] ?? null])),
      }));
    },
    async customType(name) {
      if (!schemas[name]) throw new Error(`custom object "${name}" is not defined in this portal`);
      return schemas[name];
    },
    async labelType(fromType, toType, label) {
      return { associationCategory: label ? 'USER_DEFINED' : 'HUBSPOT_DEFINED', associationTypeId: 1, label,
        fromType, toType };
    },
    async get(path) {
      guard(`GET ${path.split('?')[0]}`);
      const pipelineMatch = path.match(/^\/crm\/v3\/pipelines\/([^/?]+)$/);
      if (pipelineMatch) return { results: pipelines[pipelineMatch[1]] ?? [] };
      const objectMatch = path.match(/^\/crm\/v3\/objects\/([^/]+)\/(\d+)\?properties=(.*)$/);
      if (objectMatch) {
        const r = record(objectMatch[1], objectMatch[2]);
        return { id: r.id, properties: Object.fromEntries(objectMatch[3].split(',').map((p) => [p, r.properties[p]])) };
      }
      throw new Error(`fake api: no GET ${path}`);
    },
    async post(path, body) {
      guard(`POST ${path}`);
      const search = path.match(/^\/crm\/v3\/objects\/([^/]+)\/search$/);
      if (search) {
        const [filter] = body.filterGroups[0].filters;
        return { results: Object.values(objects[search[1]] ?? {}).filter((r) => r.properties[filter.propertyName] === filter.value) };
      }
      const create = path.match(/^\/crm\/v3\/objects\/([^/]+)$/);
      if (create) return add(create[1], body.properties);
      throw new Error(`fake api: no POST ${path}`);
    },
    async patch(path, body) {
      guard(`PATCH ${path}`);
      const [, type, id] = path.match(/^\/crm\/v3\/objects\/([^/]+)\/(\d+)$/);
      Object.assign(record(type, id).properties, body.properties);
      return record(type, id);
    },
    async put(path, types) {
      guard(`PUT ${path}`);
      const [, fromType, fromId, toType, toId] = path.match(/^\/crm\/v4\/objects\/([^/]+)\/(\d+)\/associations\/([^/]+)\/(\d+)$/);
      for (const t of types) link(fromType, fromId, toType, toId, t.label ?? null);
      return {};
    },
  };
  return { api, state, add, link, labels };
}
