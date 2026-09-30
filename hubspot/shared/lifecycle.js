// Lifecycle stage moves forward only: GrowthOps' field contract (lifecyclestage is shared between HubSpot and
// GrowthOps, and neither side may move a contact backwards). "other" is outside the ladder and is never touched.

export const LADDER = [
  'subscriber',
  'lead',
  'marketingqualifiedlead',
  'salesqualifiedlead',
  'opportunity',
  'customer',
  'evangelist',
];

export function decide(current, target) {
  if (!LADDER.includes(target)) {
    return { action: 'reject', reason: `"${target}" is not a lifecycle stage this action can set` };
  }
  if (current === 'other') {
    return { action: 'keep', reason: 'the contact is marked "other"; that is a person\'s decision, not a stage' };
  }
  const from = current ? LADDER.indexOf(current) : -1;
  if (current && from === -1) {
    return { action: 'keep', reason: `unknown current stage "${current}"; not changed` };
  }
  const to = LADDER.indexOf(target);
  if (to <= from) {
    return { action: 'keep', reason: to === from ? 'already at that stage' : 'that would move the contact backwards' };
  }
  return { action: 'move', reason: `${current || 'no stage'} to ${target}` };
}
