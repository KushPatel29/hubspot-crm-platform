// Private function: a rep accepts or dismisses a recommendation from the "Next best offer" card.
//
// Accept creates a deal for the company with the product as a line item, associated with the company, its buyer and
// the recommendation ("Converted to deal"), then marks the offer accepted. Every step is resumable, so a double
// click or a retry after a timeout never doubles the pipeline:
//   1. the deal is linked to the offer immediately after it is created, and an offer that already has a deal
//      reuses it instead of creating another;
//   2. associations are PUTs, which HubSpot treats as "ensure", not "add again";
//   3. the line item is created only if the deal has none;
//   4. the offer is marked accepted last, so "accepted" always means the deal is complete.
import { client } from '../shared/hubspot-api.js';

async function ensureDeal(api, offerType, offerId, offer, company) {
  const existing = await api.associated(offerType, offerId, 'deals');
  if (existing.length) return { dealId: existing[0].id, resumed: true };
  const products = await api.post('/crm/v3/objects/products/search', {
    filterGroups: [{ filters: [{ propertyName: 'hs_sku', operator: 'EQ', value: offer.offer_sku }] }],
    properties: ['name', 'price'], limit: 1,
  });
  const product = products.results?.[0];
  if (!product) throw new Error(`no product with SKU ${offer.offer_sku}`);
  const price = Number(product.properties.price);
  const quantity = Math.max(1, Math.round(Number(offer.offer_revenue_opportunity) / price));
  const pipelines = await api.get('/crm/v3/pipelines/deals');
  const pipeline = pipelines.results.find((p) => p.id === 'default') ?? pipelines.results[0];
  const stage = [...pipeline.stages].sort((a, b) => a.displayOrder - b.displayOrder)[0];
  const deal = await api.post('/crm/v3/objects/deals', { properties: {
    dealname: `${company.name} · ${product.properties.name} (cross-sell)`,
    amount: (quantity * price).toFixed(2), pipeline: pipeline.id, dealstage: stage.id,
  } });
  const converted = await api.labelType(offerType, 'deals', 'Converted to deal');
  await api.put(`/crm/v4/objects/${offerType}/${offerId}/associations/deals/${deal.id}`, [converted]);
  return { dealId: deal.id, resumed: false, product, quantity, price };
}

async function ensureLineItem(api, dealId, sku) {
  if ((await api.associated('deals', dealId, 'line_items')).length) return;
  const products = await api.post('/crm/v3/objects/products/search', {
    filterGroups: [{ filters: [{ propertyName: 'hs_sku', operator: 'EQ', value: sku }] }],
    properties: ['name', 'price'], limit: 1,
  });
  const product = products.results[0];
  const [deal] = await api.batchRead('deals', [dealId], ['amount']);
  const price = Number(product.properties.price);
  const quantity = Math.max(1, Math.round(Number(deal.properties.amount) / price));
  const line = await api.post('/crm/v3/objects/line_items', { properties: {
    hs_product_id: product.id, quantity: String(quantity), price: price.toFixed(2), name: product.properties.name,
  } });
  await api.put(`/crm/v4/objects/line_items/${line.id}/associations/deals/${dealId}`,
    [await api.labelType('line_items', 'deals', null)]);
}

export async function main(context, { api = client(), now = Date.now() } = {}) {
  const { offerId, decision, note = '' } = context.parameters ?? {};
  const who = context.userEmail ?? `user ${context.userId ?? 'unknown'}`;
  if (!offerId || !['accept', 'dismiss'].includes(decision)) return { ok: false, error: 'needs offerId and decision' };
  if (decision === 'dismiss' && note.trim().length < 5) return { ok: false, error: 'say why the offer is dismissed' };
  const offerType = await api.customType('recommendation');
  const [record] = await api.batchRead(offerType, [offerId], ['offer_title', 'offer_sku', 'offer_status',
    'offer_revenue_opportunity']);
  if (!record) return { ok: false, error: 'offer not found' };
  const offer = record.properties;
  const decided = {
    offer_decided_at: new Date(now).toISOString(),
    offer_decision_note: `${who}: ${note.trim() || 'accepted'}`.slice(0, 1000),
  };

  if (decision === 'dismiss') {
    if (offer.offer_status !== 'open') return { ok: false, error: `offer is already ${offer.offer_status}` };
    await api.patch(`/crm/v3/objects/${offerType}/${offerId}`, { properties: { offer_status: 'dismissed', ...decided } });
    return { ok: true, dismissed: true };
  }
  if (offer.offer_status === 'dismissed') return { ok: false, error: 'offer was dismissed' };

  const [companyLink] = await api.associated(offerType, offerId, 'companies');
  if (!companyLink) return { ok: false, error: 'offer has no company' };
  const [company] = await api.batchRead('companies', [companyLink.id], ['name']);
  const deal = await ensureDeal(api, offerType, offerId, offer, { id: company.id, name: company.properties.name });
  await api.put(`/crm/v4/objects/deals/${deal.dealId}/associations/companies/${company.id}`,
    [await api.labelType('deals', 'companies', null)]);
  const buyers = await api.associated('companies', company.id, 'contacts');
  if (buyers.length) {
    await api.put(`/crm/v4/objects/deals/${deal.dealId}/associations/contacts/${buyers[0].id}`,
      [await api.labelType('deals', 'contacts', null)]);
  }
  await ensureLineItem(api, deal.dealId, offer.offer_sku);
  if (offer.offer_status !== 'accepted') {
    await api.patch(`/crm/v3/objects/${offerType}/${offerId}`, { properties: { offer_status: 'accepted', ...decided } });
  }
  return { ok: true, dealId: deal.dealId, resumed: deal.resumed };
}
