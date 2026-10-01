import { describe, expect, it } from 'vitest';
import { band, optionValue, scoreDeal, scoreLine } from '../shared/guardrail.js';
import { decide } from '../shared/lifecycle.js';
import { allowedNext, checkTransition, deadline, describeRemaining, logEntry } from '../shared/cases.js';
import { canonicalUri, sign, verify } from '../shared/signature.js';
import { client } from '../shared/hubspot-api.js';
import { runner } from '../cards/runner.ts';

describe('guardrail', () => {
  it('derives the band from target margin, flooring at 5%', () => {
    expect(band(0.3).floor).toBeCloseTo(0.21);
    expect(band(0.3).stretch).toBeCloseTo(0.38);
    expect(band(0.1).floor).toBe(0.05);
  });

  it.each([
    [0.4, 'Above stretch', 'None'],
    [0.3, 'At target', 'None'],
    [0.28, 'Below target', 'Rep'],
    [0.25, 'Below target', 'Sales manager'],
    [0.2, 'Below floor', 'Commercial director'],
    [0.1, 'Below floor', 'VP Finance'],
    [-0.05, 'Loss-making', 'VP Finance'],
  ])('a %s margin against 30%% is %s, signed by %s', (margin, verdict, approver) => {
    const line = scoreLine(100, 100 * (1 - margin), 1, 0.3);
    expect([line.verdict, line.approver]).toEqual([verdict, approver]);
  });

  it('takes the worst line and the most senior signature', () => {
    const deal = scoreDeal([
      { price: 100, cost: 60, quantity: 1, targetMargin: 0.3 },
      { price: 100, cost: 75, quantity: 1, targetMargin: 0.3 },
      { price: 100, cost: 71, quantity: 5, targetMargin: 0.3 },
    ]);
    expect(deal.verdict).toBe('Below target');
    expect(deal.worstLine).toBe(1);
    expect(deal.approver).toBe('Sales manager');
    expect(deal.blendedMarginPct).toBeCloseTo(210 / 700);
    expect(scoreDeal([]).approver).toBe('None');
  });

  it('uses the same option values as the Python model', () => {
    expect(optionValue('Below floor')).toBe('below_floor');
    expect(optionValue('Loss-making')).toBe('loss_making');
    expect(optionValue('VP Finance')).toBe('vp_finance');
  });
});

describe('lifecycle', () => {
  it('moves forward only and never touches "other"', () => {
    expect(decide('lead', 'customer').action).toBe('move');
    expect(decide('', 'lead').action).toBe('move');
    expect(decide('customer', 'lead')).toMatchObject({ action: 'keep', reason: 'that would move the contact backwards' });
    expect(decide('customer', 'customer').action).toBe('keep');
    expect(decide('other', 'customer').action).toBe('keep');
    expect(decide('lead', 'vip').action).toBe('reject');
  });
});

describe('investigation cases', () => {
  it('allows only the defined transitions, with a note', () => {
    expect(allowedNext('Closed: no further action')).toEqual([]);
    expect(checkTransition('Open triage', 'Closed: referred for a reporting decision', 'enough words here').ok)
      .toBe(false);
    expect(checkTransition('Open triage', 'Evidence requested', 'short').ok).toBe(false);
    expect(checkTransition('Second-level review', 'Closed: referred for a reporting decision', 'pattern confirmed'))
      .toEqual({ ok: true, decision: 'referred_for_reporting_decision' });
  });

  it('runs the deadline from when the case reached the portal', () => {
    const start = Date.parse('2026-10-01T10:00:00Z');
    expect(deadline(start, 4, start + 60 * 60 * 1000).state).toBe('on_time');
    expect(deadline(start, 4, start + 3.5 * 60 * 60 * 1000).state).toBe('due_soon');
    expect(deadline('2026-10-01T10:00:00Z', 4, start + 5 * 60 * 60 * 1000).state).toBe('overdue');
    expect(describeRemaining(-90 * 60 * 1000)).toBe('90 min overdue');
    expect(deadline('not a date', 4, start).state).toBe('unknown');
    expect(deadline(String(start), 4, start + 60 * 60 * 1000).state).toBe('on_time');  // epoch ms as a string
  });

  it('appends to the activity log', () => {
    const log = logEntry('earlier line', Date.parse('2026-10-01T10:05:00Z'), 'kush@example.com', 'Open triage',
      'Evidence requested', ' need invoices ');
    expect(log).toBe('earlier line\n2026-10-01 10:05 UTC · kush@example.com: Open triage → Evidence requested. need invoices');
  });
});

describe('signature v3', () => {
  const secret = 'client-secret';
  const now = 1_790_000_000_000;
  const request = (overrides = {}) => {
    const base = { method: 'POST', uri: 'https://example.com/webhooks?x=1', body: '[{"eventId":1}]', timestamp: String(now) };
    const merged = { ...base, ...overrides };
    return { ...merged, signature: sign(secret, base.method, base.uri, base.body, base.timestamp) };
  };

  it('accepts a correctly signed request and refuses tampering, replay and the wrong secret', () => {
    expect(verify(secret, request(), now).ok).toBe(true);
    expect(verify(secret, request({ body: '[{"eventId":2}]' }), now).reason).toBe('signature mismatch');
    expect(verify(secret, request(), now + 6 * 60 * 1000).reason).toBe('stale timestamp');
    expect(verify('other', request(), now).reason).toBe('signature mismatch');
    expect(verify('', request(), now).reason).toBe('no client secret configured');
  });

  it('decodes the characters HubSpot decodes before signing', () => {
    expect(canonicalUri('https://x.com/a%3Ab%2Fc%3Fd%40e')).toBe('https://x.com/a:b/c?d@e');
  });
});

describe('card runner', () => {
  const platform = () => {
    const calls = [];
    return { calls,
      serverless: async (name, options) => { calls.push(['serverless', name, options]); return { statusCode: 200, body: { ok: true } }; },
      fetch: async (url, options) => { calls.push(['fetch', url, options]); return { status: 409, json: async () => ({ ok: false, error: 'offer was dismissed' }) }; } };
  };

  it('sends a signed action through hubspot.fetch with the record, and everything else to a private function', async () => {
    const hubspot = platform();
    const run = runner(hubspot, { decide_offer: 'https://1.hs-sites-na2.com/hs/serverless/decide-offer' }, 42);
    expect(await run('company_offers', { propertiesToSend: ['hs_object_id'] })).toEqual({ statusCode: 200, body: { ok: true } });
    expect(await run('decide_offer', { parameters: { offerId: '7', decision: 'accept' } }))
      .toEqual({ statusCode: 409, body: { ok: false, error: 'offer was dismissed' } });
    expect(hubspot.calls).toEqual([
      ['serverless', 'company_offers', { propertiesToSend: ['hs_object_id'] }],
      ['fetch', 'https://1.hs-sites-na2.com/hs/serverless/decide-offer',
        { method: 'POST', body: { offerId: '7', decision: 'accept', objectId: '42' } }],
    ]);
  });

  it('turns an answer that is not JSON into an error the card can show', async () => {
    const run = runner({ serverless: async () => ({}), fetch: async () => ({ status: 502,
      json: async () => { throw new Error('not json'); } }) }, { x: 'https://example.com/x' });
    expect(await run('x')).toEqual({ statusCode: 502, body: { ok: false, error: 'the action endpoint answered HTTP 502' } });
  });
});

describe('function API client', () => {
  const response = (status, body, headers = {}) => ({
    status, text: async () => (body === undefined ? '' : JSON.stringify(body)),
    headers: { get: (name) => headers[name.toLowerCase()] },
  });

  it('retries 429s with Retry-After, and reports errors by category without the body', async () => {
    const answers = [response(429, {}, { 'retry-after': '1' }), response(200, { ok: 1 })];
    const waits = [];
    const api = client({ token: 't', fetchImpl: async () => answers.shift(), wait: async (ms) => waits.push(ms) });
    expect(await api.get('/x')).toEqual({ ok: 1 });
    expect(waits).toEqual([1000]);
    const failing = client({ token: 't', fetchImpl: async () => response(400, {
      category: 'VALIDATION_ERROR', correlationId: 'c-1', message: 'jane@example.com is invalid' }) });
    await expect(failing.get('/y')).rejects.toThrow(/VALIDATION_ERROR \(correlation c-1\)/);
    await expect(failing.get('/y')).rejects.not.toThrow(/jane@example.com/);
  });

  it('reads associations for many records in one batch call', async () => {
    const sent = [];
    const api = client({ token: 't', fetchImpl: async (url, init) => {
      sent.push([url, JSON.parse(init.body)]);
      return response(200, { results: [{ from: { id: '1' }, to: [{ toObjectId: 9, associationTypes: [{ typeId: 3 }] }] }] });
    } });
    expect(await api.associatedBatch('2-1', ['1', '2'], 'deals')).toEqual({ 1: [{ id: '9', types: [{ typeId: 3 }] }], 2: [] });
    expect(sent).toEqual([['https://api.hubapi.com/crm/v4/associations/2-1/deals/batch/read',
      { inputs: [{ id: '1' }, { id: '2' }] }]]);
  });

  it('refuses to run without a token', () => {
    expect(() => client({ token: '' })).toThrow(/PRIVATE_APP_ACCESS_TOKEN/);
  });
});
