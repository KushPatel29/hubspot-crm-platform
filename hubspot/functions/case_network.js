// Private function behind the AML "Case evidence" card: the case's subject and the shared counterparties its
// subject transacts with, largest network first.
import { appClient, respond } from '../shared/endpoint.js';

export async function main(context, { api = appClient(context) } = {}) {
  const caseId = context.propertiesToSend?.hs_object_id ?? context.parameters?.caseId;
  if (!caseId) return respond(400, { ok: false, error: 'no case in context' });
  const caseType = await api.customType('investigation_case');
  const partyType = await api.customType('counterparty');
  // The deadline is computed from the API's raw values: a card's property hook hands datetimes over formatted for
  // display, which is no basis for arithmetic (found live: the card read "no deadline").
  const [record] = await api.batchRead(caseType, [caseId], ['hs_pipeline', 'hs_pipeline_stage', 'sla_started_at',
    'due_hours']);
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
  return respond(200, {
    ok: true,
    stage,
    clock: { startedAt: record?.properties.sla_started_at ?? null, dueHours: Number(record?.properties.due_hours) },
    subjects,
    counterparties: parties
      .map((p) => ({ id: p.id, name: p.properties.counterparty_name, type: p.properties.counterparty_type,
        region: p.properties.counterparty_region, hotSubjects: Number(p.properties.hot_subjects) }))
      .sort((a, b) => b.hotSubjects - a.hotSubjects || a.name.localeCompare(b.name)),
  });
}
