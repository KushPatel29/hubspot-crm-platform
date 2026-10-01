// Private function: an investigator moves a case to its next stage from the "Case evidence" card.
// Only the transitions in shared/cases.js are allowed, a note is required, closing sets the decision, and every
// move is appended to the case's activity log (HubSpot's property history records the same change natively).
import { appClient, respond } from '../shared/endpoint.js';
import { checkTransition, logEntry } from '../shared/cases.js';

// The move itself, whoever asks: the private function below (the card's word for who) and the signed endpoint
// (case_transition_signed.js, HubSpot's word for who) both end here.
export async function transition(api, { caseId, toStage, note } = {}, who, now) {
  if (!caseId || !toStage) return respond(400, { ok: false, error: 'needs a case and a target stage' });
  const caseType = await api.customType('investigation_case');
  const [record] = await api.batchRead(caseType, [caseId], ['hs_pipeline', 'hs_pipeline_stage', 'case_activity_log']);
  if (!record) return respond(404, { ok: false, error: 'case not found' });
  const pipelines = await api.get(`/crm/v3/pipelines/${caseType}`);
  const pipeline = pipelines.results.find((p) => p.id === record.properties.hs_pipeline);
  if (!pipeline) return respond(400, { ok: false, error: 'the case is not in a known pipeline' });
  const current = pipeline.stages.find((s) => s.id === record.properties.hs_pipeline_stage);
  const target = pipeline.stages.find((s) => s.label === toStage);
  if (!current || !target) return respond(400, { ok: false, error: `unknown stage "${toStage}"` });
  const check = checkTransition(current.label, target.label, note);
  if (!check.ok) return respond(400, { ok: false, error: check.reason });
  const properties = {
    hs_pipeline_stage: target.id,
    case_activity_log: logEntry(record.properties.case_activity_log, now, who, current.label, target.label, note),
  };
  if (check.decision) properties.decision_status = check.decision;
  await api.patch(`/crm/v3/objects/${caseType}/${caseId}`, { properties });
  return respond(200, { ok: true, from: current.label, to: target.label, decision: check.decision });
}

// Private functions get no user identity on platform 2026.09 (found live: the log read "user unknown"), so the card
// passes the signed-in user from its own context, and the log says the name is unverified.
export async function main(context, { api = appClient(context), now = Date.now() } = {}) {
  const parameters = context.parameters ?? {};
  const caseId = context.propertiesToSend?.hs_object_id ?? parameters.caseId;
  const who = context.userEmail ?? `${parameters.actor ?? `user ${context.userId ?? 'unknown'}`} (unverified)`;
  return transition(api, { caseId, toStage: parameters.toStage, note: parameters.note }, who, now);
}
