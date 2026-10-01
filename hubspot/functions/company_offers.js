// Private function behind the meat distributor's "Next best offer" card: the company's recommendations, open ones
// first by rank, with the deal each accepted one became.
import { appClient, respond } from '../shared/endpoint.js';

export const OFFER_PROPERTIES = ['offer_title', 'offer_rank', 'offer_score', 'offer_sku', 'offer_protein',
  'offer_revenue_opportunity', 'offer_because', 'offer_status', 'offer_decided_at', 'offer_decision_note'];
const STATUS_ORDER = { open: 0, accepted: 1, dismissed: 2 };

export async function main(context, { api = appClient(context) } = {}) {
  const companyId = context.propertiesToSend?.hs_object_id ?? context.parameters?.companyId;
  if (!companyId) return respond(400, { ok: false, error: 'no company in context' });
  const offerType = await api.customType('recommendation');
  const links = await api.associated('companies', companyId, offerType);
  const offers = await api.batchRead(offerType, links.map((l) => l.id), OFFER_PROPERTIES);
  const result = [];
  for (const offer of offers) {
    const p = offer.properties;
    const deals = p.offer_status === 'accepted' ? await api.associated(offerType, offer.id, 'deals') : [];
    result.push({
      id: offer.id,
      title: p.offer_title,
      rank: Number(p.offer_rank),
      sku: p.offer_sku,
      protein: p.offer_protein,
      opportunity: Number(p.offer_revenue_opportunity),
      because: p.offer_because,
      status: p.offer_status ?? 'open',
      decidedAt: p.offer_decided_at ?? null,
      note: p.offer_decision_note ?? '',
      dealId: deals[0]?.id ?? null,
    });
  }
  result.sort((a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || a.rank - b.rank);
  return respond(200, { ok: true, offers: result });
}
