"""The live parity check: every Meridian deal sent to the guardrail REST API and each answer held to Python."""

from __future__ import annotations

import json
from typing import Any

from crm_platform import guardrails
from crm_platform.salesforce import parity
from crm_platform.salesforce.load import SalesforceClient
from crm_platform.salesforce.metadata import deal_lines
from crm_platform.tenants import TENANTS

DEALS = deal_lines(TENANTS["meridian"].records())


def answer(key: str, lines: list[dict[str, float]]) -> dict[str, Any]:
    """What GuardrailApi returns for one deal: the Python guardrail stands in for the Apex one."""
    score = guardrails.score_deal([(x["price"], x["cost"], x["quantity"], x["targetMargin"]) for x in lines])
    return {"key": key, "verdict": score.verdict, "approver": score.approver, "worstLine": score.worst_line,
            "blendedMarginPct": score.blended_margin_pct, "gapDollars": score.gap_dollars,
            "lineVerdicts": [s.verdict for s in score.lines], "lineApprovers": [s.approver for s in score.lines]}


class FakeGuardrailApi:
    def __init__(self, tamper: dict[str, dict[str, Any]] | None = None, drop: str | None = None) -> None:
        self.tamper, self.drop, self.bodies = tamper or {}, drop, []

    def __call__(self, method: str, path: str, body: Any) -> tuple[int, Any, dict[str, str]]:
        assert (method, path) == ("POST", parity.SCORE)
        self.bodies.append(json.loads(json.dumps(body)))  # what would cross the wire
        results = [{**answer(deal["key"], deal["lines"]), **self.tamper.get(deal["key"], {})}
                   for deal in body["deals"] if deal["key"] != self.drop]
        return 200, {"results": results}, {}


def test_every_meridian_deal_goes_to_the_api_in_calls_of_200_and_agrees():
    api = FakeGuardrailApi()
    report = parity.check(SalesforceClient(api), DEALS)
    assert (report["deals"], report["lines"], report["calls"], report["agree"]) == (359, 480, 2, 359)
    assert report["disagree"] == {}
    assert [len(b["deals"]) for b in api.bodies] == [200, 159]
    first = api.bodies[0]["deals"][0]
    assert set(first["lines"][0]) == {"price", "cost", "quantity", "targetMargin"}


def test_a_disagreement_names_the_deal_and_the_fields_and_a_missing_answer_is_one():
    keys = sorted(DEALS)
    api = FakeGuardrailApi(tamper={keys[0]: {"approver": "Nobody", "blendedMarginPct": 0.5},
                                   keys[1]: {"gapDollars": "a lot"}}, drop=keys[2])
    report = parity.check(SalesforceClient(api), DEALS)
    assert report["disagree"] == {keys[0]: ["approver", "blendedMarginPct"], keys[1]: ["gapDollars"],
                                  keys[2]: ["missing"]}
    assert report["agree"] == 356


def test_doubles_are_compared_to_a_billionth_not_bit_for_bit():
    score = guardrails.score_deal(DEALS[sorted(DEALS)[0]])
    base = answer("k", [{"price": p, "cost": c, "quantity": q, "targetMargin": t}
                        for p, c, q, t in DEALS[sorted(DEALS)[0]]])
    assert parity.differences(score, {**base, "blendedMarginPct": score.blended_margin_pct + 1e-12}) == []
    assert parity.differences(score, {**base, "blendedMarginPct": score.blended_margin_pct + 1e-6}) == [
        "blendedMarginPct"]
