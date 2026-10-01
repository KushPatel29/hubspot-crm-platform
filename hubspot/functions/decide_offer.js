// Private function: a rep accepts or dismisses a recommendation from the "Next best offer" card.
//
// Accept creates a deal for the company with the product as a line item, associated with the company, its buyer and
// the recommendation ("Converted to deal"), then marks the offer accepted. It is safe to run twice, at once or after
// a failure part way through, so a double click, two reps on the same offer, or a retry never doubles the pipeline:
//   1. the deal carries an idempotency key (crm_platform_key = "offer-deal:<offer id>"), a unique property, so
//      HubSpot itself refuses a second deal for the same offer. The request that loses that race finds the winner's
//      deal by the key, makes sure it is linked to the offer, and leaves the rest to the winner;
//   2. the deal is created already linked to the offer, company and buyer (associations in the create call), so no
//      deal exists unlinked even for a moment, and a later run finds it from the offer;
//   3. the line item is created, linked to the deal in the same call, only if the deal has none;
//   4. the offer is marked accepted last, so "accepted" always means the deal is complete.
import { appClient, respond } from '../shared/endpoint.js';

export const KEY_PROPERTY = 'crm_platform_key';
export const dealKey = (offerId) => `offer-deal:${offerId}`;

async function productFor(api, sku) {
  const products = await api.post('/crm/v3/objects/products/search', {
    filterGroups: [{ filters: [{ propertyName: 'hs_sku', operator: 'EQ', value: sku }] }],
    properties: ['name', 'price'], limit: 1,
  });
  const product = products.results?.[0];
  if (!product) throw new Error(`no product with SKU ${sku}`);
  return product;
}

const link = (id, type) => ({ to: { id: String(id) }, types: [type] });

async function dealByKey(api, key) {
  try {
    return await api.get(`/crm/v3/objects/deals/${encodeURIComponent(key)}?idProperty=${KEY_PROPERTY}&properties=amount`);
  } catch (error) {
    if (error.status === 404) return null;
    throw error;
  }
}

// { dealId, how }: "resumed" (the offer already has its deal), "created", or "raced" (another request created it).
async function ensureDeal(api, offerType, offerId, offer, company, buyerId) {
  const existing = await api.associated(offerType, offerId, 'deals');
  if (existing.length) return { dealId: existing[0].id, how: 'resumed' };
  const key = dealKey(offerId);
  const product = await productFor(api, offer.offer_sku);
  const price = Number(product.properties.price);
  const quantity = Math.max(1, Math.round(Number(offer.offer_revenue_opportunity) / price));
  const pipelines = await api.get('/crm/v3/pipelines/deals');
  const pipeline = pipelines.results.find((p) => p.id === 'default') ?? pipelines.results[0];
  const stage = [...pipeline.stages].sort((a, b) => a.displayOrder - b.displayOrder)[0];
  const toOffer = await api.labelType('deals', offerType, 'Source recommendation');
  const associations = [link(offerId, toOffer), link(company.id, await api.labelType('deals', 'companies', null))];
  if (buyerId) associations.push(link(buyerId, await api.labelType('deals', 'contacts', null)));
  try {
    const deal = await api.post('/crm/v3/objects/deals', {
      properties: {
        dealname: `${company.name} · ${product.properties.name} (cross-sell)`,
        amount: (quantity * price).toFixed(2), pipeline: pipeline.id, dealstage: stage.id, [KEY_PROPERTY]: key,
      },
      associations,
    });
    return { dealId: String(deal.id), how: 'created' };
  } catch (error) {
    // The key is unique, so a refused create may mean this offer's deal already exists: another request won the
    // race, or a create whose response was lost did succeed and the client's retry was refused.
    const winner = error.status >= 400 && error.status < 500 ? await dealByKey(api, key) : null;
    if (!winner) throw error;
    await api.put(`/crm/v4/objects/deals/${winner.id}/associations/${offerType}/${offerId}`, [toOffer]);
    return { dealId: String(winner.id), how: 'raced' };
  }
}

async function ensureLineItem(api, dealId, sku) {
  if ((await api.associated('deals', dealId, 'line_items')).length) return;
  const product = await productFor(api, sku);
  const [deal] = await api.batchRead('deals', [dealId], ['amount']);
  const price = Number(product.properties.price);
  const quantity = Math.max(1, Math.round(Number(deal.properties.amount) / price));
  await api.post('/crm/v3/objects/line_items', {
    properties: {
      hs_product_id: product.id, quantity: String(quantity), price: price.toFixed(2), name: product.properties.name,
    },
    associations: [link(dealId, await api.labelType('line_items', 'deals', null))],
  });
}

// The decision itself, whoever asks: the private function below (the card's word for who) and the signed endpoint
// (decide_offer_signed.js, HubSpot's word for who) both end here.
export async function decide(api, { offerId, decision, note = '' } = {}, who, now) {
  if (!offerId || !['accept', 'dismiss'].includes(decision)) {
    return respond(400, { ok: false, error: 'needs offerId and decision' });
  }
  if (decision === 'dismiss' && note.trim().length < 5) {
    return respond(400, { ok: false, error: 'say why the offer is dismissed' });
  }
  const offerType = await api.customType('recommendation');
  const [record] = await api.batchRead(offerType, [offerId], ['offer_title', 'offer_sku', 'offer_status',
    'offer_revenue_opportunity']);
  if (!record) return respond(404, { ok: false, error: 'offer not found' });
  const offer = record.properties;
  const decided = {
    offer_decided_at: new Date(now).toISOString(),
    offer_decision_note: `${who}: ${note.trim() || 'accepted'}`.slice(0, 1000),
  };

  if (decision === 'dismiss') {
    if (offer.offer_status !== 'open') {
      return respond(409, { ok: false, error: `offer is already ${offer.offer_status}` });
    }
    await api.patch(`/crm/v3/objects/${offerType}/${offerId}`, { properties: { offer_status: 'dismissed', ...decided } });
    return respond(200, { ok: true, dismissed: true });
  }
  if (offer.offer_status === 'dismissed') return respond(409, { ok: false, error: 'offer was dismissed' });

  const [companyLink] = await api.associated(offerType, offerId, 'companies');
  if (!companyLink) return respond(400, { ok: false, error: 'offer has no company' });
  const [company] = await api.batchRead('companies', [companyLink.id], ['name']);
  const [buyer] = await api.associated('companies', company.id, 'contacts');
  const deal = await ensureDeal(api, offerType, offerId, offer, { id: company.id, name: company.properties.name },
    buyer?.id);
  if (deal.how === 'raced') {
    // The request that created the deal is finishing it; doing its line item here too could double it.
    return respond(200, { ok: true, dealId: deal.dealId, resumed: true, inProgress: true });
  }
  if (deal.how === 'resumed') {
    // A deal from an earlier, interrupted run: make sure it is linked as a new one would be. PUTs are "ensure".
    await api.put(`/crm/v4/objects/deals/${deal.dealId}/associations/companies/${company.id}`,
      [await api.labelType('deals', 'companies', null)]);
    if (buyer) {
      await api.put(`/crm/v4/objects/deals/${deal.dealId}/associations/contacts/${buyer.id}`,
        [await api.labelType('deals', 'contacts', null)]);
    }
  }
  await ensureLineItem(api, deal.dealId, offer.offer_sku);
  if (offer.offer_status !== 'accepted') {
    await api.patch(`/crm/v3/objects/${offerType}/${offerId}`, { properties: { offer_status: 'accepted', ...decided } });
  }
  return respond(200, { ok: true, dealId: deal.dealId, resumed: deal.how === 'resumed' });
}

// Private function: HubSpot gives it no user identity, so the name is the card's and is recorded as unverified.
export async function main(context, { api = appClient(context), now = Date.now() } = {}) {
  const parameters = context.parameters ?? {};
  const who = context.userEmail ?? `${parameters.actor ?? `user ${context.userId ?? 'unknown'}`} (unverified)`;
  return decide(api, parameters, who, now);
}
