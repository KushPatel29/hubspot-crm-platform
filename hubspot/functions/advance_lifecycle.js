// Custom workflow action "Advance lifecycle stage (never backwards)" for ScaleLab, the GrowthOps portal.
// HubSpot's own "set property" action would happily move a customer back to lead; this one enforces GrowthOps'
// field contract (lifecyclestage is shared and moves forward only) and says what it did.
import { decide } from '../shared/lifecycle.js';
import { appClient, authenticate, parsedBody, respond } from '../shared/endpoint.js';

export const PATH = '/hs/serverless/advance-lifecycle';

export async function main(context, { api, now = Date.now(), clientSecret } = {}) {
  const auth = authenticate(context, PATH, { clientSecret, now });
  if (!auth.ok) return respond(401, { error: auth.reason });
  const request = parsedBody(context);
  const contactId = request.object?.objectId;
  const target = request.inputFields?.target_stage;
  if (!contactId || !target) return respond(400, { error: 'needs a contact and a target_stage' });
  const hubspot = api ?? appClient(context);
  const contact = await hubspot.get(`/crm/v3/objects/contacts/${contactId}?properties=lifecyclestage`);
  const current = contact.properties?.lifecyclestage ?? '';
  const decision = decide(current, target);
  if (decision.action === 'move') {
    // HubSpot refuses to set an earlier stage directly and some portals refuse a jump; clearing first is not
    // needed going forward, so a single write is enough.
    await hubspot.patch(`/crm/v3/objects/contacts/${contactId}`, { properties: { lifecyclestage: target } });
  }
  return respond(200, { outputFields: {
    outcome: decision.action === 'move' ? 'moved' : decision.action === 'keep' ? 'kept' : 'rejected',
    from_stage: current || 'none',
    reason: decision.reason,
  } });
}
