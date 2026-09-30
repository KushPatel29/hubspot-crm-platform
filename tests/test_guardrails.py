"""Meridian's guardrail, held to the pricing project's rules."""

from __future__ import annotations

import pytest

from crm_platform.guardrails import band, score_deal, score_line


def test_band_is_derived_from_target_margin_as_the_pricing_project_does():
    assert band(0.30) == pytest.approx((0.21, 0.30, 0.38))
    assert band(0.10) == pytest.approx((0.05, 0.10, 0.18))  # the floor never goes under 5%


@pytest.mark.parametrize(("margin", "verdict", "approver"), [
    (0.40, "Above stretch", "None"),
    (0.30, "At target", "None"),
    (0.28, "Below target", "Rep"),  # exactly two points under target
    (0.25, "Below target", "Sales manager"),  # five points
    (0.20, "Below floor", "Commercial director"),  # ten points
    (0.10, "Below floor", "VP Finance"),
    (-0.05, "Loss-making", "VP Finance"),
])
def test_verdicts_and_approval_tiers(margin, verdict, approver):
    price = 100.0
    line = score_line(price, price * (1 - margin), 1, 0.30)
    assert (line.verdict, line.approver) == (verdict, approver)


def test_margin_gap_dollars_is_extended_by_quantity():
    line = score_line(100.0, 75.0, 10, 0.30)  # 25% against 30%: five points on $1,000
    assert line.gap_dollars == pytest.approx(50.0)


def test_a_deal_takes_its_worst_line_and_its_most_senior_signature():
    # 40% (above stretch), 25% (five points under target) and 29% (one point under, on five units)
    deal = score_deal([(100.0, 60.0, 1, 0.30), (100.0, 75.0, 1, 0.30), (100.0, 71.0, 5, 0.30)])
    assert deal.verdict == "Below target" and deal.worst_line == 1
    assert deal.approver == "Sales manager"
    assert deal.blended_margin_pct == pytest.approx((40 + 25 + 145) / 700)
    assert score_deal([(100.0, 60.0, 1, 0.30), (100.0, 85.0, 1, 0.30)]).approver == "VP Finance"
    assert score_deal([]).approver == "None"
