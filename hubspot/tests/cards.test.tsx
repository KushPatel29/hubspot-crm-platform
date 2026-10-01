import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import {
  Alert, Button, DescriptionListItem, ErrorState, StatisticsItem, StatusTag, TableRow, Text, TextArea,
} from '@hubspot/ui-extensions';
import { createRenderer } from '@hubspot/ui-extensions/testing';
import { RevenueTruthCard } from '../cards/RevenueTruthCard.tsx';
import { DealMarginCard } from '../cards/DealMarginCard.tsx';
import { NextBestOfferCard } from '../cards/NextBestOfferCard.tsx';
import { CaseEvidenceCard } from '../cards/CaseEvidenceCard.tsx';

const loaded = (properties: Record<string, string | null>) => ({
  properties, isLoading: false, isRefetching: false, error: null, refetch: async () => {},
});

describe('Revenue truth card', () => {
  it('shows cash, where the attribution models disagree, and renewal risk', () => {
    const { render, mocks, find, findAll } = createRenderer('crm.record.tab');
    mocks.useCrmProperties.willCall(() => loaded({
      growthops_contact_id: 'c-1', growthops_net_cash: '1500', lifecyclestage: 'customer',
      growthops_first_touch_campaign: 'meta_broad_v17', growthops_lead_creation_campaign: 'webinar_q3',
      growthops_last_non_direct_campaign: 'webinar_q3', growthops_tracking_status: 'complete',
      growthops_has_closed_won: 'true', growthops_renewal_risk: 'high', growthops_renewal_due_date: '2026-10-05',
    }));
    mocks.useAssociations.willCall(() => ({ results: [
      { toObjectId: 1, associationTypes: [], properties: { growthops_product: 'accelerator' } },
      { toObjectId: 2, associationTypes: [], properties: { growthops_product: 'community' } }],
    error: null, isLoading: false, isRefetching: false, refetch: async () => {},
    pagination: { hasNextPage: false, hasPreviousPage: false, currentPage: 1, pageSize: 10, nextPage: () => {},
      previousPage: () => {}, reset: () => {} } }) as never);
    render(<RevenueTruthCard />);
    expect(find(StatisticsItem, { label: 'Net cash collected' }).props.number).toBe('$1,500');
    expect(findAll(Text).some((t) => t.text?.includes('models disagree'))).toBe(true);
    expect(find(StatusTag).text).toBe('High risk');
    expect(find(DescriptionListItem, { label: 'Bought' }).text).toBe('Accelerator, Community');
    expect(findAll(Alert)).toHaveLength(0);  // "complete" tracking raises no warning
  });

  it('warns when the landing page dropped the UTMs', () => {
    const { render, mocks, find } = createRenderer('crm.record.tab');
    mocks.useCrmProperties.willCall(() => loaded({ growthops_contact_id: 'c-2', growthops_tracking_status: 'missing_utm' }));
    render(<RevenueTruthCard />);
    expect(find(Alert).props.title).toBe('Attribution is incomplete');
    expect(find(Alert).text).toContain('dropped its UTM parameters');
  });

  it('says so instead of showing zeros for a contact GrowthOps did not create', () => {
    const { render, mocks, find } = createRenderer('crm.record.tab');
    mocks.useCrmProperties.willCall(() => loaded({ growthops_contact_id: null }));
    render(<RevenueTruthCard />);
    expect(find(Text).text).toContain('not created by GrowthOps');
  });
});

const margin = {
  ok: true, basis: 'Invoice margin.', unscored: [],
  deal: { blendedMarginPct: 0.3, verdict: 'Below target', approver: 'Sales manager', gapDollars: 10, worstLine: 1 },
  lines: [
    { id: '1', name: 'Kettle', quantity: 1, price: 100, marginPct: 0.4, verdict: 'Above stretch', approver: 'None',
      floor: 0.21, target: 0.3, gapDollars: 0 },
    { id: '2', name: 'Toaster', quantity: 2, price: 100, marginPct: 0.25, verdict: 'Below target',
      approver: 'Sales manager', floor: 0.21, target: 0.3, gapDollars: 10 },
  ],
};

describe('Deal margin card', () => {
  it('names the verdict, the approver and the line that sets it', async () => {
    const run = vi.fn(async () => ({ statusCode: 200, body: margin }));  // the shape an app function answers
    const { render, find, findAll, waitFor } = createRenderer('crm.record.tab');
    render(<DealMarginCard run={run} />);
    await waitFor(() => expect(find(Alert).props.title).toBe('Below target: Sales manager must approve'));
    expect(find(Alert).text).toContain('Toaster sets the verdict at 25.0%');
    expect(findAll(TableRow)).toHaveLength(3);
    expect(run).toHaveBeenCalledWith('deal_margin', { propertiesToSend: ['hs_object_id'] });
  });

  it('shows the failure and retries on request', async () => {
    const run = vi.fn().mockRejectedValueOnce(new Error('function timed out')).mockResolvedValue(margin);
    const { render, find, maybeFind, waitFor } = createRenderer('crm.record.tab');
    render(<DealMarginCard run={run} />);
    await waitFor(() => expect(find(ErrorState).find(Text).text).toBe('function timed out'));
    find(Button).trigger('onClick');
    await waitFor(() => expect(maybeFind(ErrorState)).toBeNull());
    expect(run).toHaveBeenCalledTimes(2);
  });
});

describe('Next best offer card', () => {
  const offers = [
    { id: '11', title: '#1 Back Ribs (Pork) for Aurora', rank: 1, opportunity: 524.54, because: 'Whistler Izakaya 018',
      status: 'open', note: '', dealId: null },
    { id: '12', title: '#2 Lamb Leg', rank: 2, opportunity: 558.8, because: 'Whistler Izakaya 018',
      status: 'accepted', note: '', dealId: '99' },
  ];

  it('accepts an offer into a deal and needs a reason to dismiss one', async () => {
    const run = vi.fn(async (name: string) => (name === 'company_offers' ? { ok: true, offers } : { ok: true }));
    const { render, mocks, find, waitFor } = createRenderer('crm.record.tab');
    mocks.useCrmProperties.willCall(() => loaded({ xsell_churn_risk: 'high', xsell_days_overdue: '14',
      xsell_rfm_segment: 'at_risk' }));
    render(<NextBestOfferCard run={run} portalId={42} />);
    await waitFor(() => expect(find(Button, { variant: 'primary' }).text).toBe('Create deal'));
    expect(find(Alert).props.title).toBe('High churn risk');
    find(Button, { variant: 'primary' }).trigger('onClick');
    await waitFor(() => expect(run).toHaveBeenCalledWith('decide_offer',
      { parameters: { offerId: '11', decision: 'accept', note: '', actor: undefined } }));
    await waitFor(() => expect(find(Button, { variant: 'secondary' }).props.disabled).toBe(false));
    find(Button, { variant: 'secondary' }).trigger('onClick');
    await waitFor(() => expect(find(Button, { variant: 'destructive' }).props.disabled).toBe(true));
    find(TextArea).trigger('onInput', 'not stocked in BC' as never);
    await waitFor(() => expect(find(Button, { variant: 'destructive' }).props.disabled).toBe(false));
  });
});

describe('Case evidence card', () => {
  const properties = {
    case_name: 'INV-ENT-00060 · Becky Horton', case_priority: 'p0', due_hours: '4',
    sla_started_at: '2026-10-01T10:00:00Z', typology_hypothesis: 't02_layering',
    evidence_references: 'R2_LAYERING: $146,627.62 inbound dispersed to 6 counterparties',
    recommended_next_step: 'Validate source of funds.', amount_involved_cad: '293913.58',
    decision_status: 'human_decision_required', hs_pipeline_stage: 's0',
  };
  const network = (stage: string) => ({ ok: true, stage, clock: { startedAt: '2026-10-01T10:00:00Z', dueHours: 4 },
    subjects: [{ id: '1', name: 'Becky Horton',
    objectType: 'contacts' }], counterparties: [{ id: '5', name: 'Big hub (CPT-1)', type: 'supplier', hotSubjects: 30 }] });

  it('shows the deadline, the hypothesis against its lawful lookalike, and only the allowed moves', async () => {
    const run = vi.fn(async () => network('Open triage'));
    const { render, mocks, find, findAll, waitFor } = createRenderer('crm.record.tab');
    mocks.useCrmProperties.willCall(() => loaded(properties));
    render(<CaseEvidenceCard run={run} now={Date.parse('2026-10-01T13:30:00Z')} />);
    await waitFor(() => expect(findAll(Button).map((b) => b.text)).toEqual(
      ['Evidence requested', 'Second-level review', 'Closed: no further action']));
    expect(findAll(StatusTag).map((t) => t.text)).toEqual(['P0', '30 min left']);
    expect(find(Alert).text).toContain('Wholesale settlement');
  });

  it('asks for a reason before moving a case, and offers nothing on a closed one', async () => {
    const run = vi.fn(async (name: string) => (name === 'case_network' ? network('Open triage') : { ok: true,
      to: 'Evidence requested' }));
    const { render, mocks, find, waitFor } = createRenderer('crm.record.tab');
    mocks.useCrmProperties.willCall(() => loaded(properties));
    render(<CaseEvidenceCard run={run} actor="inv@example.com" now={Date.parse('2026-10-01T11:00:00Z')} />);
    await waitFor(() => expect(find(Button, { variant: 'primary' }).text).toBe('Evidence requested'));
    find(Button, { variant: 'primary' }).trigger('onClick');
    await waitFor(() => expect(find(Button, { variant: 'primary' }).props.disabled).toBe(true));
    find(TextArea).trigger('onInput', 'need the supplier invoices' as never);
    await waitFor(() => expect(find(Button, { variant: 'primary' }).props.disabled).toBe(false));
    find(Button, { variant: 'primary' }).trigger('onClick');
    await waitFor(() => expect(run).toHaveBeenCalledWith('case_transition', { propertiesToSend: ['hs_object_id'],
      parameters: { toStage: 'Evidence requested', note: 'need the supplier invoices', actor: 'inv@example.com' } }));

    const closed = createRenderer('crm.record.tab');
    closed.mocks.useCrmProperties.willCall(() => loaded(properties));
    closed.render(<CaseEvidenceCard run={async () => network('Closed: no further action')} />);
    await closed.waitFor(() => expect(closed.find(Text, (t) => t.text === 'Closed: no further action')).toBeTruthy());
    expect(closed.findAll(Button)).toHaveLength(0);
  });
});
