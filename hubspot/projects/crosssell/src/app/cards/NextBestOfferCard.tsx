// Meat distributor company card: the cross-sell engine's next-best offers for this account, why each one, and
// what it is worth, with the account's churn context. A rep accepts an offer (it becomes a deal with the product
// as a line item) or dismisses it with a reason; both are written back to the Recommendation record.
import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Button, ButtonRow, Divider, ErrorState, Flex, Link, LoadingSpinner, StatusTag, Text, TextArea,
} from '@hubspot/ui-extensions';
import { useCrmProperties } from '@hubspot/ui-extensions/crm';
import { label, money, num, call, type Runner } from './format.ts';

type Offer = { id: string; title: string; rank: number; opportunity: number; because: string; status: string;
  note: string; dealId: string | null };

const STATUS: Record<string, 'info' | 'success' | 'default'> = { open: 'info', accepted: 'success', dismissed: 'default' };

export function NextBestOfferCard({ run, portalId }: { run: Runner; portalId?: number }) {
  const { properties: account } = useCrmProperties(['xsell_churn_risk', 'xsell_days_overdue', 'xsell_rfm_segment']);
  const [offers, setOffers] = useState<Offer[] | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [dismissing, setDismissing] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    call(run, 'company_offers', { propertiesToSend: ['hs_object_id'] })
      .then((r) => (r.ok ? setOffers(r.offers) : setFailure(r.error)))
      .catch((e: Error) => setFailure(e.message));
  }, [run]);
  useEffect(load, [load]);

  const decide = (offerId: string, decision: 'accept' | 'dismiss', note = '') => {
    setBusy(offerId);
    call(run, 'decide_offer', { parameters: { offerId, decision, note } })
      .then((r) => {
        if (!r.ok) setFailure(r.error);
        setDismissing(null);
        setReason('');
        load();
      })
      .catch((e: Error) => setFailure(e.message))
      .finally(() => setBusy(null));
  };

  if (failure) return <ErrorState title="Offers could not be loaded"><Text>{failure}</Text></ErrorState>;
  if (!offers) return <LoadingSpinner label="Loading next-best offers" />;
  const overdue = num(account.xsell_days_overdue);
  return (
    <Flex direction="column" gap="sm">
      {account.xsell_churn_risk && account.xsell_churn_risk !== 'low' && (
        <Alert title={`${label(account.xsell_churn_risk)} churn risk`} variant="warning">
          {label(account.xsell_rfm_segment)}{overdue && overdue > 0 ? `, ${overdue} days past the usual reorder` : ''}.
          Lead with the reorder before the cross-sell.
        </Alert>
      )}
      {offers.length === 0 && <Text>No offer passed the engine's eligibility checks for this account.</Text>}
      {offers.map((offer) => (
        <Flex key={offer.id} direction="column" gap="xs">
          <Flex gap="sm" align="center" justify="between">
            <Text format={{ fontWeight: 'demibold' }}>{offer.title}</Text>
            <StatusTag variant={STATUS[offer.status] ?? 'default'}>{label(offer.status)}</StatusTag>
          </Flex>
          <Text variant="microcopy">
            {money(offer.opportunity)} a year at list · accounts like {offer.because} buy it
          </Text>
          {offer.status === 'open' && dismissing !== offer.id && (
            <ButtonRow>
              <Button variant="primary" disabled={busy !== null} onClick={() => decide(offer.id, 'accept')}>
                {busy === offer.id ? 'Creating deal…' : 'Create deal'}
              </Button>
              <Button variant="secondary" disabled={busy !== null} onClick={() => setDismissing(offer.id)}>Dismiss</Button>
            </ButtonRow>
          )}
          {dismissing === offer.id && (
            <Flex direction="column" gap="xs">
              <TextArea label="Why is it not a fit?" name={`reason-${offer.id}`} value={reason} onChange={setReason}
                required />
              <ButtonRow>
                <Button variant="destructive" disabled={reason.trim().length < 5 || busy !== null}
                  onClick={() => decide(offer.id, 'dismiss', reason)}>Dismiss offer</Button>
                <Button variant="transparent" onClick={() => setDismissing(null)}>Cancel</Button>
              </ButtonRow>
            </Flex>
          )}
          {offer.dealId && portalId && (
            <Link href={`https://app.hubspot.com/contacts/${portalId}/record/0-3/${offer.dealId}`}>Open the deal</Link>
          )}
          {offer.status === 'dismissed' && offer.note && <Text variant="microcopy">{offer.note}</Text>}
          <Divider />
        </Flex>
      ))}
    </Flex>
  );
}
