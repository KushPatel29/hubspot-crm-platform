// Meridian deal card: each line's margin against its product's guardrail band, the deal's verdict (its worst
// line) and who has to sign (the most senior signature any line needs). Scored by the deal_margin function.
import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Button, ErrorState, Flex, LoadingSpinner, Statistics, StatisticsItem, Table, TableBody, TableCell,
  TableHead, TableHeader, TableRow, Tag, Text,
} from '@hubspot/ui-extensions';
import { money, pct, call, type Runner } from './format.ts';

type Line = { id: string; name: string; quantity: number; price: number; marginPct: number; verdict: string;
  approver: string; floor: number; target: number; gapDollars: number };
type Result = { ok: boolean; error?: string; basis: string; unscored: string[];
  deal: { blendedMarginPct: number; verdict: string; approver: string; gapDollars: number; worstLine: number };
  lines: Line[] };

const TONE: Record<string, 'success' | 'default' | 'warning' | 'error'> = {
  'Above stretch': 'success', 'At target': 'success', 'Below target': 'warning', 'Below floor': 'error',
  'Loss-making': 'error',
};

export function DealMarginCard({ run }: { run: Runner }) {
  const [result, setResult] = useState<Result | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const load = useCallback(() => {
    setFailure(null);
    call(run, 'deal_margin', { propertiesToSend: ['hs_object_id'] })
      .then((r: Result) => (r.ok ? setResult(r) : setFailure(r.error ?? 'unknown error')))
      .catch((e: Error) => setFailure(e.message));
  }, [run]);
  useEffect(load, [load]);

  if (failure) {
    return (
      <ErrorState title="The margin check could not run">
        <Text>{failure}</Text>
        <Button onClick={load}>Try again</Button>
      </ErrorState>
    );
  }
  if (!result) return <LoadingSpinner label="Scoring the deal's lines" />;
  if (result.lines.length === 0) return <Text>This deal has no priced line items yet.</Text>;
  const { deal } = result;
  const signature = deal.approver === 'None' ? 'No signature needed' : `${deal.approver} must approve`;
  return (
    <Flex direction="column" gap="sm">
      <Statistics>
        <StatisticsItem label="Blended margin" number={pct(deal.blendedMarginPct)} />
        <StatisticsItem label="Margin under target" number={money(deal.gapDollars)} />
      </Statistics>
      <Alert title={`${deal.verdict}: ${signature}`}
        variant={deal.verdict === 'Below floor' || deal.verdict === 'Loss-making' ? 'danger'
          : deal.approver === 'None' ? 'success' : 'warning'}>
        {deal.worstLine >= 0 && deal.approver !== 'None'
          ? `${result.lines[deal.worstLine].name} sets the verdict at ${pct(result.lines[deal.worstLine].marginPct)}.`
          : 'Every line is at or above its target margin.'}
      </Alert>
      <Table bordered>
        <TableHead>
          <TableRow>
            <TableHeader>Line</TableHeader>
            <TableHeader>Margin</TableHeader>
            <TableHeader>Floor / target</TableHeader>
            <TableHeader>Verdict</TableHeader>
          </TableRow>
        </TableHead>
        <TableBody>
          {result.lines.map((line) => (
            <TableRow key={line.id}>
              <TableCell>{line.name}</TableCell>
              <TableCell>{pct(line.marginPct)}</TableCell>
              <TableCell>{`${pct(line.floor, 0)} / ${pct(line.target, 0)}`}</TableCell>
              <TableCell><Tag variant={TONE[line.verdict] ?? 'default'}>{line.verdict}</Tag></TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {result.unscored.length > 0 && (
        <Text variant="microcopy">Not scored (no product or cost): {result.unscored.join(', ')}.</Text>
      )}
      <Text variant="microcopy">{result.basis}</Text>
    </Flex>
  );
}
