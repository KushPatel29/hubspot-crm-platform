// Investigation case rules: the deadline clock and which stage a case may move to next.
// Mirrors crm_platform/tenants/aml.py (pipeline "Investigation"). A closed case is not reopened from the card;
// reopening is a supervisor's decision made in HubSpot itself, where the stage history records who did it.

export const STAGES = [
  'Open triage',
  'Evidence requested',
  'Second-level review',
  'Closed: no further action',
  'Closed: referred for a reporting decision',
];

export const TRANSITIONS = {
  'Open triage': ['Evidence requested', 'Second-level review', 'Closed: no further action'],
  'Evidence requested': ['Second-level review', 'Closed: no further action'],
  'Second-level review': ['Evidence requested', 'Closed: no further action', 'Closed: referred for a reporting decision'],
  'Closed: no further action': [],
  'Closed: referred for a reporting decision': [],
};

export const DECISIONS = {
  'Closed: no further action': 'no_further_action',
  'Closed: referred for a reporting decision': 'referred_for_reporting_decision',
};

export function allowedNext(stage) {
  return TRANSITIONS[stage] ?? [];
}

export function checkTransition(from, to, note) {
  if (!allowedNext(from).includes(to)) {
    return { ok: false, reason: `a case in "${from}" cannot move to "${to}"` };
  }
  if (!note || note.trim().length < 10) {
    return { ok: false, reason: 'say why in at least ten characters; the note is the audit trail' };
  }
  return { ok: true, decision: DECISIONS[to] ?? null };
}

const HOUR = 3600 * 1000;

// startedAt: ISO string, epoch ms, or epoch ms as a string (how a card's CRM property hook passes a datetime, found
// live: the card read "no deadline" until this was handled); dueHours: number; now: epoch ms
export function toEpoch(value) {
  if (typeof value === 'number') return value;
  const text = String(value ?? '').trim();
  return /^\d+$/.test(text) ? Number(text) : Date.parse(text);
}

export function deadline(startedAt, dueHours, now) {
  const start = toEpoch(startedAt);
  if (!Number.isFinite(start) || !Number.isFinite(Number(dueHours))) {
    return { state: 'unknown', dueAt: null, remainingMs: null };
  }
  const dueAt = start + Number(dueHours) * HOUR;
  const remainingMs = dueAt - now;
  const soon = Math.min(2 * HOUR, (Number(dueHours) * HOUR) / 4);
  const state = remainingMs < 0 ? 'overdue' : remainingMs <= soon ? 'due_soon' : 'on_time';
  return { state, dueAt, remainingMs };
}

export function describeRemaining(remainingMs) {
  if (remainingMs === null) return 'no deadline';
  const late = remainingMs < 0;
  const minutes = Math.round(Math.abs(remainingMs) / 60000);
  const text = minutes >= 120 ? `${Math.round(minutes / 60)} h` : `${minutes} min`;
  return late ? `${text} overdue` : `${text} left`;
}

export function logEntry(existing, when, who, from, to, note) {
  const line = `${new Date(when).toISOString().slice(0, 16).replace('T', ' ')} UTC · ${who}: ${from} → ${to}. ${note.trim()}`;
  return existing ? `${existing}\n${line}` : line;
}
