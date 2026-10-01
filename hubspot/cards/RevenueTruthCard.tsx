// ScaleLab (GrowthOps) contact card: what this person is actually worth in collected cash, which campaign each
// attribution model credits, where they are in the funnel, and the renewal risk GrowthOps computes. Everything is
// read from properties GrowthOps' sync keeps current (the contact's, and its deals' through the association), so
// the card needs no app function.
import React from 'react';
import {
  Alert, DescriptionList, DescriptionListItem, Divider, ErrorState, Flex, LoadingSpinner, Statistics,
  StatisticsItem, StatusTag, Text,
} from '@hubspot/ui-extensions';
import { useAssociations, useCrmProperties } from '@hubspot/ui-extensions/crm';
import { day, label, money, num } from './format.ts';

export const PROPERTIES = [
  'growthops_contact_id', 'growthops_net_cash', 'growthops_original_source', 'growthops_first_touch_campaign',
  'growthops_lead_creation_campaign', 'growthops_last_non_direct_campaign', 'growthops_tracking_status',
  'growthops_mql_date', 'growthops_call_booked_date', 'growthops_has_closed_won', 'growthops_renewal_due_date',
  'growthops_renewal_risk', 'lifecyclestage',
];
// What was bought lives on the deals (GrowthOps writes growthops_product there), read through the association.
export const DEAL_PROPERTIES = ['dealname', 'growthops_product', 'growthops_net_cash'];

// GrowthOps' tracking statuses that mean the campaign credit below cannot be trusted ("complete" and "direct" are
// fine: direct traffic legitimately has no campaign).
export const TRACKING_PROBLEMS: Record<string, string> = {
  missing_utm: 'the landing page dropped its UTM parameters',
  off_taxonomy: 'the UTM values are outside the campaign taxonomy',
  no_lead_touch: 'no touch was recorded before the lead was created',
};

const RISK: Record<string, 'danger' | 'warning' | 'success' | 'default'> = {
  high: 'danger', medium: 'warning', not_due: 'success',
};

export function RevenueTruthCard() {
  const { properties: p, isLoading, error } = useCrmProperties(PROPERTIES);
  const { results: deals } = useAssociations({ toObjectType: '0-3', properties: DEAL_PROPERTIES, pageLength: 10 });
  if (isLoading) return <LoadingSpinner label="Loading GrowthOps data" />;
  if (error) return <ErrorState title="GrowthOps data could not be loaded"><Text>{error.message}</Text></ErrorState>;
  if (!p.growthops_contact_id) {
    return <Text>This contact was not created by GrowthOps, so there is no revenue history to reconcile.</Text>;
  }
  const models = [
    ['First touch', p.growthops_first_touch_campaign],
    ['Lead creation', p.growthops_lead_creation_campaign],
    ['Last non-direct', p.growthops_last_non_direct_campaign],
  ] as const;
  const credited = new Set(models.map(([, campaign]) => campaign).filter(Boolean));
  const risk = p.growthops_renewal_risk ?? '';
  return (
    <Flex direction="column" gap="sm">
      <Statistics>
        <StatisticsItem label="Net cash collected" number={money(num(p.growthops_net_cash))} />
        <StatisticsItem label="Lifecycle stage" number={label(p.lifecyclestage)} />
      </Statistics>
      {TRACKING_PROBLEMS[p.growthops_tracking_status ?? ''] && (
        <Alert title="Attribution is incomplete" variant="warning">
          For this contact {TRACKING_PROBLEMS[p.growthops_tracking_status ?? '']}, so the campaigns below may not be the
          whole story.
        </Alert>
      )}
      <Text format={{ fontWeight: 'demibold' }}>Which campaign gets the credit</Text>
      <DescriptionList direction="row">
        {models.map(([model, campaign]) => (
          <DescriptionListItem key={model} label={model}>{campaign || '—'}</DescriptionListItem>
        ))}
      </DescriptionList>
      {credited.size > 1 && (
        <Text variant="microcopy">
          The attribution models disagree about this contact; GrowthOps reports cash under each model, not one.
        </Text>
      )}
      <Divider />
      <DescriptionList direction="row">
        <DescriptionListItem label="Original source">{label(p.growthops_original_source)}</DescriptionListItem>
        <DescriptionListItem label="Became an MQL">{day(p.growthops_mql_date)}</DescriptionListItem>
        <DescriptionListItem label="Booked a call">{day(p.growthops_call_booked_date)}</DescriptionListItem>
        <DescriptionListItem label="Bought">
          {p.growthops_has_closed_won === 'true'
            ? [...new Set(deals.map((d) => label(d.properties.growthops_product)).filter((v) => v !== '—'))].join(', ')
              || 'Yes'
            : 'Not yet'}
        </DescriptionListItem>
      </DescriptionList>
      {risk && (
        <Flex gap="sm" align="center">
          <Text>Renewal {day(p.growthops_renewal_due_date)}</Text>
          <StatusTag variant={RISK[risk] ?? 'default'}>{risk === 'not_due' ? 'Not due' : `${label(risk)} risk`}</StatusTag>
        </Flex>
      )}
    </Flex>
  );
}
