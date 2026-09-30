// AML investigation case card: the deadline, the typology hypothesis next to its lawful lookalike and the evidence
// that would separate them, the shared counterparties, and the moves an investigator can make from here. Synthetic
// data; the card never decides or files anything, it records a person's decision with their reason.
import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Button, ButtonRow, DescriptionList, DescriptionListItem, Divider, ErrorState, Flex, LoadingSpinner,
  StatusTag, Table, TableBody, TableCell, TableHead, TableHeader, TableRow, Text, TextArea,
} from '@hubspot/ui-extensions';
import { useCrmProperties } from '@hubspot/ui-extensions/crm';
import { allowedNext, deadline, describeRemaining } from '../shared/cases.js';
import { TYPOLOGIES } from './generated/typologies.ts';
import { label, money, num, type Runner } from './format.ts';

export const PROPERTIES = ['case_name', 'case_priority', 'due_hours', 'sla_started_at', 'typology_hypothesis',
  'evidence_references', 'recommended_next_step', 'amount_involved_cad', 'decision_status', 'hs_pipeline_stage'];

type Network = { ok: boolean; error?: string; stage?: string | null; subjects: { id: string; name: string; objectType: string }[];
  counterparties: { id: string; name: string; type: string; hotSubjects: number }[] };
const DEADLINE: Record<string, 'danger' | 'warning' | 'success' | 'default'> = {
  overdue: 'danger', due_soon: 'warning', on_time: 'success', unknown: 'default',
};

export function CaseEvidenceCard({ run, now = Date.now() }: { run: Runner; now?: number }) {
  const { properties: p, isLoading, refetch } = useCrmProperties(PROPERTIES);
  const [network, setNetwork] = useState<Network | null>(null);
  const [moving, setMoving] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(() => {
    run('case_network', { propertiesToSend: ['hs_object_id'] }).then(setNetwork)
      .catch((e: Error) => setNetwork({ ok: false, error: e.message, subjects: [], counterparties: [] }));
  }, [run]);
  useEffect(load, [load]);

  if (isLoading || !network) return <LoadingSpinner label="Loading the case" />;
  if (!network.ok) return <ErrorState title="The case network could not be loaded"><Text>{network.error}</Text></ErrorState>;
  const clock = deadline(p.sla_started_at ?? '', num(p.due_hours) ?? NaN, now);
  const typology = TYPOLOGIES[(p.typology_hypothesis ?? '').toUpperCase().replace(/_/g, '-')];
  const stage = network.stage ?? null;
  const closed = stage?.startsWith('Closed');

  const move = () => {
    if (!moving) return;
    run('case_transition', { propertiesToSend: ['hs_object_id'], parameters: { toStage: moving, note } })
      .then((r) => {
        setMessage(r.ok ? { ok: true, text: `Moved to ${r.to}.` } : { ok: false, text: r.error });
        if (r.ok) {
          setMoving(null);
          setNote('');
          refetch();
          load();
        }
      })
      .catch((e: Error) => setMessage({ ok: false, text: e.message }));
  };

  return (
    <Flex direction="column" gap="sm">
      <Flex gap="sm" align="center">
        <StatusTag variant={p.case_priority === 'p0' ? 'danger' : 'default'}>{(p.case_priority ?? '').toUpperCase()}</StatusTag>
        <Text format={{ fontWeight: 'demibold' }}>{stage ?? 'Stage unknown'}</Text>
        {!closed && <StatusTag variant={DEADLINE[clock.state] ?? 'default'}>{describeRemaining(clock.remainingMs)}</StatusTag>}
      </Flex>
      <DescriptionList direction="row">
        <DescriptionListItem label="Amount involved">{money(num(p.amount_involved_cad))}</DescriptionListItem>
        <DescriptionListItem label="Subject">{network.subjects.map((s) => s.name).join(', ') || '—'}</DescriptionListItem>
        <DescriptionListItem label="Decision">{label(p.decision_status)}</DescriptionListItem>
      </DescriptionList>
      <Text format={{ fontWeight: 'demibold' }}>Evidence</Text>
      <Text>{p.evidence_references || 'Model score only; no rule fired.'}</Text>
      {typology ? (
        <Alert title={`Hypothesis: ${typology.hypothesis}`} variant="info">
          Lawful lookalike: {typology.lawfulLookalike} What separates them: {typology.discriminator}
        </Alert>
      ) : (
        <Text variant="microcopy">No typology hypothesis: this case was raised by the model score alone.</Text>
      )}
      {network.counterparties.length > 0 && (
        <Table bordered>
          <TableHead>
            <TableRow>
              <TableHeader>Shared counterparty</TableHeader>
              <TableHeader>Type</TableHeader>
              <TableHeader>Flagged subjects using it</TableHeader>
            </TableRow>
          </TableHead>
          <TableBody>
            {network.counterparties.slice(0, 8).map((c) => (
              <TableRow key={c.id}>
                <TableCell>{c.name}</TableCell>
                <TableCell>{label(c.type)}</TableCell>
                <TableCell>{c.hotSubjects}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <Text variant="microcopy">Next step: {p.recommended_next_step}</Text>
      <Divider />
      {message && <Alert title={message.ok ? 'Done' : 'Not moved'} variant={message.ok ? 'success' : 'error'}>{message.text}</Alert>}
      {stage && !closed && !moving && (
        <ButtonRow>
          {allowedNext(stage).map((next: string) => (
            <Button key={next} variant={next.startsWith('Closed') ? 'secondary' : 'primary'} onClick={() => setMoving(next)}>
              {next}
            </Button>
          ))}
        </ButtonRow>
      )}
      {moving && (
        <Flex direction="column" gap="xs">
          <TextArea label={`Why move this case to "${moving}"?`} name="transition-note" value={note} onChange={setNote}
            required description="Saved to the case's activity log with your name and the time." />
          <ButtonRow>
            <Button variant="primary" disabled={note.trim().length < 10} onClick={move}>Move case</Button>
            <Button variant="transparent" onClick={() => setMoving(null)}>Cancel</Button>
          </ButtonRow>
        </Flex>
      )}
      <Text variant="microcopy">Synthetic data. An educational simulation of case management, not a compliance tool.</Text>
    </Flex>
  );
}
