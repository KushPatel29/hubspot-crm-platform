"""Meridian's deal guardrail, ported from the pricing project so HubSpot enforces the same rule.

Source: ``pricing/guardrails.py`` and ``engine/build_pricing_analytics.py`` in KushPatel29/pricing-costing-analytics.
The rules carried over unchanged:

* A product's band comes from its target margin: floor ``max(0.05, target - 0.09)``, stretch ``target + 0.08``.
* A line's verdict is where its margin sits in that band: above stretch, at target, below target, below floor,
  or loss-making.
* **Approval is tiered on the gap to target, not on the discount:** up to 2 points a rep can sign, 5 a sales
  manager, 10 a commercial director, anything more the VP Finance.

One difference, stated wherever the result is shown: the pricing project measures *pocket* margin, after rebates,
freight and terms. HubSpot holds the quoted (invoice) price and the cost of goods, not the off-invoice deductions,
which live in the ERP. So the guardrail here runs on invoice margin, which is the rule applied to the price the CRM
knows. A deal is only as clean as its worst line: its verdict is the worst line's, and its approver is the most senior
signature any line needs.

The same rule runs in HubSpot as JavaScript (``hubspot/shared/guardrail.js``); a test runs both over every Meridian
line and requires identical answers.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

APPROVAL_TIERS: tuple[tuple[float, str], ...] = (
    (0.02, "Rep"),
    (0.05, "Sales manager"),
    (0.10, "Commercial director"),
    (float("inf"), "VP Finance"),
)
APPROVERS = ("None", "Rep", "Sales manager", "Commercial director", "VP Finance")
VERDICTS = ("Above stretch", "At target", "Below target", "Below floor", "Loss-making")  # best to worst
TOLERANCE = 1e-9
# The band around a product's target margin. Salesforce reads the same numbers, and the approval tiers' limits, from
# the Meridian_Guardrail_Setting__mdt record "Default"; a test holds that record to these.
FLOOR_MINIMUM = 0.05
FLOOR_DROP = 0.09
STRETCH_RISE = 0.08


def band(target_margin: float) -> tuple[float, float, float]:
    """(floor, target, stretch) for a product, as the pricing project derives it."""
    return max(FLOOR_MINIMUM, target_margin - FLOOR_DROP), target_margin, target_margin + STRETCH_RISE


@dataclass(frozen=True)
class LineScore:
    margin_pct: float
    verdict: str
    approver: str
    gap: float  # margin points under target (negative when above)
    gap_dollars: float  # the margin the approver is asked to give up, extended by quantity
    revenue: float
    margin: float


def score_line(price: float, cost: float, quantity: float, target_margin: float) -> LineScore:
    floor, target, stretch = band(target_margin)
    revenue = price * quantity
    margin_pct = (price - cost) / price if price > 0 else 0.0
    if margin_pct >= stretch - TOLERANCE:
        verdict = "Above stretch"
    elif margin_pct >= target - TOLERANCE:
        verdict = "At target"
    elif margin_pct >= floor - TOLERANCE:
        verdict = "Below target"
    elif price - cost > 0:
        verdict = "Below floor"
    else:
        verdict = "Loss-making"
    gap = target - margin_pct
    approver = "None"
    if gap > TOLERANCE:
        approver = next(name for limit, name in APPROVAL_TIERS if gap <= limit)
    return LineScore(margin_pct, verdict, approver, gap, max(0.0, gap) * revenue, revenue, (price - cost) * quantity)


@dataclass(frozen=True)
class DealScore:
    blended_margin_pct: float
    verdict: str
    approver: str
    gap_dollars: float
    worst_line: int  # index of the line that sets the verdict
    lines: tuple[LineScore, ...]


def score_deal(lines: Iterable[tuple[float, float, float, float]]) -> DealScore:
    """Score a deal from its lines: (price, cost, quantity, target margin) each."""
    scored = tuple(score_line(*line) for line in lines)
    if not scored:
        return DealScore(0.0, "At target", "None", 0.0, -1, ())
    revenue = sum(s.revenue for s in scored)
    worst = max(range(len(scored)), key=lambda i: (VERDICTS.index(scored[i].verdict), scored[i].gap, -i))
    approver = max((s.approver for s in scored), key=APPROVERS.index)
    return DealScore(sum(s.margin for s in scored) / revenue if revenue > 0 else 0.0, scored[worst].verdict, approver,
                     sum(s.gap_dollars for s in scored), worst, scored)
