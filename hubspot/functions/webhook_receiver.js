// ScaleLab's webhook target: HubSpot's own deliveries for contact changes, deletions, merges and privacy deletions.
// It verifies the v3 signature and logs one structured line per delivery (event IDs, types, objects), which
// `hs project logs` shows. The GrowthOps worker applies events by refetching the record (growthops/
// hubspot_webhooks.py); this receiver is what proves HubSpot delivers, signs and retries them to a live endpoint.
import { authenticate, parsedBody, respond } from '../shared/endpoint.js';

export const PATH = '/hs/serverless/webhooks';

export function summarise(events) {
  const byType = {};
  for (const event of events) byType[event.subscriptionType] = (byType[event.subscriptionType] ?? 0) + 1;
  return {
    events: events.length,
    byType,
    eventIds: events.map((e) => e.eventId).slice(0, 20),
    objects: [...new Set(events.map((e) => e.objectId))].slice(0, 20),
    attempts: Math.max(0, ...events.map((e) => Number(e.attemptNumber ?? 0))),
  };
}

export async function main(context, { now = Date.now(), secret, log = console.log } = {}) {
  const auth = authenticate(context, PATH, { secret, now });
  if (!auth.ok) {
    log(JSON.stringify({ webhook: 'refused', reason: auth.reason }));
    return respond(401, { error: auth.reason });
  }
  const events = parsedBody(context);
  if (!Array.isArray(events)) return respond(400, { error: 'expected an array of events' });
  const summary = summarise(events);
  log(JSON.stringify({ webhook: 'accepted', ...summary }));
  return respond(200, { accepted: summary.events });
}
