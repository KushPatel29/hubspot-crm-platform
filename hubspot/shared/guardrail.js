// Meridian's deal guardrail, the same rule as crm_platform/guardrails.py (a Python test runs both over every
// Meridian quote line and requires identical answers). Ported from the pricing project:
//   band from target margin: floor max(5%, target - 9 points), stretch target + 8 points;
//   verdict = where the margin sits in the band; approval tiered on the gap to target (2 / 5 / 10 points).
// HubSpot holds the quoted price and cost of goods, not rebates or freight, so this is invoice margin.

export const APPROVAL_TIERS = [
  [0.02, 'Rep'],
  [0.05, 'Sales manager'],
  [0.1, 'Commercial director'],
  [Infinity, 'VP Finance'],
];
export const APPROVERS = ['None', 'Rep', 'Sales manager', 'Commercial director', 'VP Finance'];
export const VERDICTS = ['Above stretch', 'At target', 'Below target', 'Below floor', 'Loss-making'];
const TOLERANCE = 1e-9;

export function band(targetMargin) {
  return { floor: Math.max(0.05, targetMargin - 0.09), target: targetMargin, stretch: targetMargin + 0.08 };
}

export function scoreLine(price, cost, quantity, targetMargin) {
  const { floor, target, stretch } = band(targetMargin);
  const revenue = price * quantity;
  const marginPct = price > 0 ? (price - cost) / price : 0;
  let verdict;
  if (marginPct >= stretch - TOLERANCE) verdict = 'Above stretch';
  else if (marginPct >= target - TOLERANCE) verdict = 'At target';
  else if (marginPct >= floor - TOLERANCE) verdict = 'Below target';
  else if (price - cost > 0) verdict = 'Below floor';
  else verdict = 'Loss-making';
  const gap = target - marginPct;
  let approver = 'None';
  if (gap > TOLERANCE) approver = APPROVAL_TIERS.find(([limit]) => gap <= limit)[1];
  return {
    marginPct,
    verdict,
    approver,
    gap,
    gapDollars: Math.max(0, gap) * revenue,
    revenue,
    margin: (price - cost) * quantity,
    floor,
    target,
    stretch,
  };
}

// lines: [{ price, cost, quantity, targetMargin }]
export function scoreDeal(lines) {
  const scored = lines.map((l) => scoreLine(l.price, l.cost, l.quantity, l.targetMargin));
  if (scored.length === 0) {
    return { blendedMarginPct: 0, verdict: 'At target', approver: 'None', gapDollars: 0, worstLine: -1, lines: [] };
  }
  const revenue = scored.reduce((sum, s) => sum + s.revenue, 0);
  let worst = 0;
  scored.forEach((s, i) => {
    const w = scored[worst];
    const rank = VERDICTS.indexOf(s.verdict) - VERDICTS.indexOf(w.verdict);
    if (rank > 0 || (rank === 0 && s.gap > w.gap)) worst = i;
  });
  const approver = scored.reduce(
    (best, s) => (APPROVERS.indexOf(s.approver) > APPROVERS.indexOf(best) ? s.approver : best),
    'None',
  );
  return {
    blendedMarginPct: revenue > 0 ? scored.reduce((sum, s) => sum + s.margin, 0) / revenue : 0,
    verdict: scored[worst].verdict,
    approver,
    gapDollars: scored.reduce((sum, s) => sum + s.gapDollars, 0),
    worstLine: worst,
    lines: scored,
  };
}

// The option values the Meridian model uses for these labels (crm_platform.model.slug).
export function optionValue(label) {
  return label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}
