"""Hold the org's Apex guardrail to the Python one, live, through the API another system would use.

Every Meridian deal's lines go to the guardrail REST API (``GuardrailApi``:
``POST /services/apexrest/meridian/guardrail/v1/score``, 200 deals a call) and each answer is compared with
:mod:`crm_platform.guardrails`: verdict, approver, worst line, every line's verdict and approver, and the blended
margin and margin given up to 1e-9.

    python -m crm_platform.salesforce.parity meridian --org crm-dev

``GuardrailServiceTest`` makes the same comparison inside a deploy, from a static resource. This one runs against
the code as deployed, with the org's own guardrail settings (``Meridian_Guardrail_Setting__mdt``), so an admin who
changes a threshold in the org and nowhere else is caught here. It writes
``evidence/<tenant>/salesforce_parity.json``: counts and the keys of any deal that disagrees, never a price.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, datetime
from typing import Any

from crm_platform import guardrails
from crm_platform.salesforce.load import EVIDENCE, SalesforceClient, cli_transport, org_guard
from crm_platform.salesforce.metadata import HAND_WRITTEN, deal_lines
from crm_platform.tenants import TENANTS

SCORE = "/services/apexrest/meridian/guardrail/v1/score"
PER_CALL = 200


def differences(expected: guardrails.DealScore, answer: dict[str, Any] | None) -> list[str]:
    """The fields on which the API's answer differs from the Python guardrail's (all of them if there is none)."""
    if answer is None:
        return ["missing"]
    exact: dict[str, object] = {"verdict": expected.verdict, "approver": expected.approver,
                                "worstLine": expected.worst_line,
                                "lineVerdicts": [line.verdict for line in expected.lines],
                                "lineApprovers": [line.approver for line in expected.lines]}
    close: dict[str, float] = {"blendedMarginPct": expected.blended_margin_pct, "gapDollars": expected.gap_dollars}
    out = [field for field, want in exact.items() if answer.get(field) != want]
    for field, value in close.items():
        got = answer.get(field)
        if not isinstance(got, int | float) or not math.isclose(got, value, rel_tol=1e-12, abs_tol=1e-9):
            out.append(field)
    return out


def check(client: SalesforceClient, deals: dict[str, list[tuple[float, float, float, float]]]) -> dict[str, Any]:
    keys = sorted(deals)
    disagree: dict[str, list[str]] = {}
    for start in range(0, len(keys), PER_CALL):
        chunk = keys[start:start + PER_CALL]
        body = {"deals": [{"key": key, "lines": [
            {"price": price, "cost": cost, "quantity": quantity, "targetMargin": target}
            for price, cost, quantity, target in deals[key]]} for key in chunk]}
        answers = {result.get("key"): result for result in client.call("POST", SCORE, body).get("results", [])}
        for key in chunk:
            fields = differences(guardrails.score_deal(deals[key]), answers.get(key))
            if fields:
                disagree[key] = fields
    return {"endpoint": SCORE, "deals": len(keys), "lines": sum(len(lines) for lines in deals.values()),
            "calls": -(-len(keys) // PER_CALL), "agree": len(keys) - len(disagree), "disagree": disagree}


def run(tenant_key: str, client: SalesforceClient) -> dict[str, Any]:
    org = org_guard(client, tenant_key, binding=False)
    report = check(client, deal_lines(TENANTS[tenant_key].records()))
    return {"tenant": tenant_key, "org": org, "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), **report}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("tenant", choices=sorted(HAND_WRITTEN))
    parser.add_argument("--org", required=True, help="the Salesforce CLI alias of the org")
    parser.add_argument("--evidence", action="store_true", help="write evidence/<tenant>/salesforce_parity.json")
    args = parser.parse_args(argv)
    report = run(args.tenant, SalesforceClient(cli_transport(args.org)))
    if args.evidence:
        path = EVIDENCE / args.tenant / "salesforce_parity.json"
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    sys.exit(0 if not report["disagree"] else 1)


if __name__ == "__main__":
    main()
