import { describe, expect, it } from 'vitest';
import { fakeApi } from './fake-api.js';
import { sign } from '../shared/signature.js';
import { main as dealMargin } from '../functions/deal_margin.js';
import { main as priceGuardrail } from '../functions/price_guardrail.js';
import { main as advanceLifecycle } from '../functions/advance_lifecycle.js';
import { main as webhookReceiver, summarise } from '../functions/webhook_receiver.js';
import { main as companyOffers } from '../functions/company_offers.js';
import { main as decideOffer } from '../functions/decide_offer.js';
import { main as caseNetwork } from '../functions/case_network.js';
import { main as caseTransition } from '../functions/case_transition.js';

const SECRET = 'client-secret';
const NOW = Date.parse('2026-10-01T12:00:00Z');
const BASE = 'https://1234.hs-sites-na2.com';

function signed(path, body) {
  const raw = JSON.stringify(body);
  process.env.ENDPOINT_BASE_URL = BASE;
  return {
    method: 'POST', body: raw, query: {},
    headers: { 'X-HubSpot-Request-Timestamp': String(NOW),
      'X-HubSpot-Signature-v3': sign(SECRET, 'POST', `${BASE}${path}`, raw, String(NOW)) },
  };
}
const output = (response) => response.body;

function meridianDeal() {
  const fake = fakeApi();
  const deal = fake.add('deals', { dealname: 'RFQ' });
  const good = fake.add('products', { name: 'Kettle', meridian_target_margin: '0.30' });
  const thin = fake.add('products', { name: 'Toaster', meridian_target_margin: '0.30' });
  for (const [product, price, cost, qty] of [[good, '100', '60', '1'], [thin, '100', '75', '2']]) {
    const line = fake.add('line_items', { name: product.properties.name, price, hs_cost_of_goods_sold: cost,
      quantity: qty, hs_product_id: product.id });
    fake.link('deals', deal.id, 'line_items', line.id);
  }
  return { fake, deal };
}

describe('deal margin and the price guardrail', () => {
  it('scores each line against its product and the deal on its worst line', async () => {
    const { fake, deal } = meridianDeal();
    const { statusCode, body: result } = await dealMargin({ propertiesToSend: { hs_object_id: deal.id } },
      { api: fake.api });
    expect(statusCode).toBe(200);
    expect(result.deal).toMatchObject({ verdict: 'Below target', approver: 'Sales manager' });
    expect(result.lines.map((l) => l.name)).toEqual(['Kettle', 'Toaster']);
    expect(result.basis).toMatch(/Invoice margin/);
  });

  it('refuses an unsigned or forged workflow request before touching the deal', async () => {
    const { fake, deal } = meridianDeal();
    const request = signed('/price-guardrail', { object: { objectId: deal.id } });
    request.body = request.body.replace(deal.id, '999');
    const response = await priceGuardrail(request, { api: fake.api, now: NOW, secret: SECRET });
    expect(response.statusCode).toBe(401);
    expect(fake.state.calls).toEqual([]);
  });

  it('writes the verdict to the deal and returns fields a workflow can branch on', async () => {
    const { fake, deal } = meridianDeal();
    const response = await priceGuardrail(signed('/price-guardrail', { object: { objectId: deal.id } }),
      { api: fake.api, now: NOW, secret: SECRET });
    expect(response.statusCode).toBe(200);
    expect(output(response).outputFields).toMatchObject({ verdict: 'below_target', approver: 'sales_manager',
      needs_approval: 'true' });
    expect(fake.state.objects.deals[deal.id].properties).toMatchObject({ meridian_guardrail_verdict: 'below_target',
      meridian_approver: 'sales_manager' });
  });
});

describe('advance lifecycle', () => {
  it('moves forward, keeps backwards moves out, and says which', async () => {
    const fake = fakeApi();
    const contact = fake.add('contacts', { lifecyclestage: 'customer' });
    const back = await advanceLifecycle(signed('/advance-lifecycle', { object: { objectId: contact.id },
      inputFields: { target_stage: 'lead' } }), { api: fake.api, now: NOW, secret: SECRET });
    expect(output(back).outputFields.outcome).toBe('kept');
    expect(fake.state.objects.contacts[contact.id].properties.lifecyclestage).toBe('customer');
    const forward = await advanceLifecycle(signed('/advance-lifecycle', { object: { objectId: contact.id },
      inputFields: { target_stage: 'evangelist' } }), { api: fake.api, now: NOW, secret: SECRET });
    expect(output(forward).outputFields).toMatchObject({ outcome: 'moved', from_stage: 'customer' });
  });
});

describe('webhook receiver', () => {
  it('accepts signed deliveries and logs a summary, never the payload values', async () => {
    const lines = [];
    const events = [{ eventId: 1, subscriptionType: 'contact.propertyChange', objectId: 7, propertyValue: 'x@y.com' },
      { eventId: 2, subscriptionType: 'contact.privacyDeletion', objectId: 8, attemptNumber: 1 }];
    const response = await webhookReceiver(signed('/webhooks', events), { now: NOW, secret: SECRET,
      log: (line) => lines.push(line) });
    expect(response.statusCode).toBe(200);
    expect(lines[0]).not.toContain('x@y.com');
    expect(JSON.parse(lines[0])).toMatchObject({ webhook: 'accepted', events: 2, attempts: 1 });
    expect(summarise(events).byType).toEqual({ 'contact.propertyChange': 1, 'contact.privacyDeletion': 1 });
    const forged = await webhookReceiver({ ...signed('/webhooks', events), body: '[]' }, { now: NOW, secret: SECRET,
      log: () => {} });
    expect(forged.statusCode).toBe(401);
  });
});

function crossSell() {
  const fake = fakeApi({ schemas: { recommendation: '2-9' }, pipelines: { deals: [{ id: 'default', stages: [
    { id: 'closedwon', displayOrder: 5 }, { id: 'appointmentscheduled', displayOrder: 0 }] }] } });
  const company = fake.add('companies', { name: 'Aurora Kaiseki 001' });
  const buyer = fake.add('contacts', { firstname: 'Ana' });
  fake.link('contacts', buyer.id, 'companies', company.id, 'Buyer');
  fake.add('products', { name: 'Back Ribs', hs_sku: 'SKU-012', price: '10.49' });
  const offers = [1, 2].map((rank) => {
    const offer = fake.add('2-9', { offer_title: `#${rank}`, offer_rank: String(rank), offer_sku: 'SKU-012',
      offer_status: 'open', offer_revenue_opportunity: '524.54' });
    fake.link('2-9', offer.id, 'companies', company.id, 'Recommended for');
    return offer;
  });
  return { fake, company, offers };
}

describe('next best offer', () => {
  it('lists open offers first by rank', async () => {
    const { fake, company, offers } = crossSell();
    fake.state.objects['2-9'][offers[0].id].properties.offer_status = 'dismissed';
    const { body: result } = await companyOffers({ propertiesToSend: { hs_object_id: company.id } }, { api: fake.api });
    expect(result.offers.map((o) => [o.rank, o.status])).toEqual([[2, 'open'], [1, 'dismissed']]);
  });

  it('turns an offer into one complete deal, even when clicked twice', async () => {
    const { fake, offers } = crossSell();
    const context = { parameters: { offerId: offers[0].id, decision: 'accept' }, userEmail: 'rep@example.com' };
    const first = (await decideOffer(context, { api: fake.api, now: NOW })).body;
    const second = (await decideOffer(context, { api: fake.api, now: NOW })).body;
    expect(first.ok && second.ok).toBe(true);
    expect(second.dealId).toBe(first.dealId);
    expect(Object.keys(fake.state.objects.deals)).toHaveLength(1);
    const deal = fake.state.objects.deals[first.dealId].properties;
    expect(deal).toMatchObject({ amount: '524.50', dealstage: 'appointmentscheduled' });
    expect(Object.keys(fake.state.objects.line_items)).toHaveLength(1);
    expect(fake.state.objects['2-9'][offers[0].id].properties.offer_status).toBe('accepted');
    const labels = fake.state.links.filter((l) => l.fromType === '2-9' && l.toType === 'deals').map((l) => l.label);
    expect(labels).toEqual(['Converted to deal']);
  });

  it('resumes after a failure part way through instead of creating a second deal', async () => {
    const { fake, offers } = crossSell();
    const context = { parameters: { offerId: offers[0].id, decision: 'accept' } };
    fake.state.failNext = 'POST /crm/v3/objects/line_items';
    await expect(decideOffer(context, { api: fake.api, now: NOW })).rejects.toThrow(/injected/);
    expect(fake.state.objects['2-9'][offers[0].id].properties.offer_status).toBe('open');
    const retry = (await decideOffer(context, { api: fake.api, now: NOW })).body;
    expect(retry).toMatchObject({ ok: true, resumed: true });
    expect(Object.keys(fake.state.objects.deals)).toHaveLength(1);
    expect(Object.keys(fake.state.objects.line_items)).toHaveLength(1);
  });

  it('needs a reason to dismiss', async () => {
    const { fake, offers } = crossSell();
    const refused = await decideOffer({ parameters: { offerId: offers[1].id, decision: 'dismiss', note: '' } },
      { api: fake.api });
    expect([refused.statusCode, refused.body.ok]).toEqual([400, false]);
    expect((await decideOffer({ parameters: { offerId: offers[1].id, decision: 'dismiss', note: 'not stocked' } },
      { api: fake.api })).body.ok).toBe(true);
  });
});

function amlCase() {
  const stages = ['Open triage', 'Evidence requested', 'Second-level review', 'Closed: no further action',
    'Closed: referred for a reporting decision'].map((label, i) => ({ id: `s${i}`, label, displayOrder: i }));
  const fake = fakeApi({ schemas: { investigation_case: '2-1', counterparty: '2-2' },
    pipelines: { '2-1': [{ id: 'p1', stages }] } });
  const record = fake.add('2-1', { hs_pipeline: 'p1', hs_pipeline_stage: 's0', case_activity_log: '' });
  const subject = fake.add('contacts', { firstname: 'Becky', lastname: 'Horton' });
  fake.link('2-1', record.id, 'contacts', subject.id, 'Subject');
  for (const [name, hot] of [['Small hub', '2'], ['Big hub', '30']]) {
    const party = fake.add('2-2', { counterparty_name: name, counterparty_type: 'supplier', hot_subjects: hot });
    fake.link('2-1', record.id, '2-2', party.id, 'Transacted with');
  }
  return { fake, record };
}

describe('investigation cases', () => {
  it('shows the subject and the largest shared counterparties first', async () => {
    const { fake, record } = amlCase();
    const { body: result } = await caseNetwork({ propertiesToSend: { hs_object_id: record.id } }, { api: fake.api });
    expect(result.subjects.map((s) => s.name)).toEqual(['Becky Horton']);
    expect(result.counterparties.map((c) => c.name)).toEqual(['Big hub', 'Small hub']);
  });

  it('moves a case only along allowed transitions, and logs who moved it and why', async () => {
    const { fake, record } = amlCase();
    const { body: refused } = await caseTransition({ propertiesToSend: { hs_object_id: record.id },
      parameters: { toStage: 'Closed: referred for a reporting decision', note: 'looks bad to me' } },
    { api: fake.api, now: NOW });
    expect(refused.ok).toBe(false);
    const { body: moved } = await caseTransition({ propertiesToSend: { hs_object_id: record.id }, userEmail: 'inv@example.com',
      parameters: { toStage: 'Evidence requested', note: 'need the supplier invoices' } }, { api: fake.api, now: NOW });
    expect(moved).toMatchObject({ ok: true, from: 'Open triage', to: 'Evidence requested' });
    const props = fake.state.objects['2-1'][record.id].properties;
    expect(props.hs_pipeline_stage).toBe('s1');
    expect(props.case_activity_log).toContain('inv@example.com: Open triage → Evidence requested. need the supplier');
  });
});
