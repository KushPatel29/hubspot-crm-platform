// Private function behind the AML "Case evidence" card: the case's subject and the shared counterparties its
// subject transacts with, largest network first.
import { client } from '../shared/hubspot-api.js';

export async function main(context, { api = client() } = {}) {
  const caseId = context.propertiesToSend?.hs_object_id ?? context.parameters?.caseId;
  if (!caseId) return { ok: false, error: 'no case in context' };
  const caseType = await api.customType('investigation_case');
  const partyType = await api.customType('counterparty');
  const [record] = await api.batchRead(caseType, [caseId], ['hs_pipeline', 'hs_pipeline_stage']);
  const pipelines = await api.get(`/crm/v3/pipelines/${caseType}`);
  const pipeline = (pipelines.results ?? []).find((p) => p.id === record?.properties.hs_pipeline);
  const stage = pipeline?.stages.find((s) => s.id === record.properties.hs_pipeline_stage)?.label ?? null;
  const links = await api.associated(caseType, caseId, partyType);
  const parties = await api.batchRead(partyType, links.map((l) => l.id), ['counterparty_name', 'counterparty_type',
    'counterparty_region', 'hot_subjects']);
  const subjects = [];
  for (const [objectType, name] of [['contacts', ['firstname', 'lastname']], ['companies', ['name']]]) {
    const ids = await api.associated(caseType, caseId, objectType);
    for (const entry of await api.batchRead(objectType, ids.map((l) => l.id), name)) {
      const p = entry.properties;
      subjects.push({ id: entry.id, objectType, name: p.name ?? `${p.firstname ?? ''} ${p.lastname ?? ''}`.trim() });
    }
  }
  return {
    ok: true,
    stage,
    subjects,
    counterparties: parties
      .map((p) => ({ id: p.id, name: p.properties.counterparty_name, type: p.properties.counterparty_type,
        region: p.properties.counterparty_region, hotSubjects: Number(p.properties.hot_subjects) }))
      .sort((a, b) => b.hotSubjects - a.hotSubjects || a.name.localeCompare(b.name)),
  };
}
