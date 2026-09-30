"""The guardrail HubSpot runs (JavaScript, in the app functions) and the one the loader runs (Python) agree exactly."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import pytest

from crm_platform import guardrails
from crm_platform.tenants import TENANTS

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = """
import { readFileSync } from 'node:fs';
import { scoreDeal } from './hubspot/shared/guardrail.js';
const deals = JSON.parse(readFileSync(0, 'utf8'));
const out = deals.map((lines) => {
  const s = scoreDeal(lines.map(([price, cost, quantity, targetMargin]) => ({ price, cost, quantity, targetMargin })));
  return [s.blendedMarginPct, s.verdict, s.approver, s.gapDollars, s.worstLine,
          s.lines.map((l) => [l.marginPct, l.verdict, l.approver, l.gapDollars])];
});
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_python_and_javascript_score_every_meridian_deal_identically():
    records = TENANTS["meridian"].records()
    targets = {r.key: float(r.properties["meridian_target_margin"]) for r in records if r.object_name == "products"}
    deals: dict[str, list[tuple[float, float, float, float]]] = defaultdict(list)
    for r in records:
        if r.object_name == "line_items":
            product = r.properties["hs_product_id"].rsplit(":", 1)[1]
            deals[r.links[0].to_key].append((float(r.properties["price"]), float(r.properties["hs_cost_of_goods_sold"]),
                                             float(r.properties["quantity"]), targets[product]))
    ordered = [deals[k] for k in sorted(deals)]
    result = subprocess.run(["node", "--input-type=module", "-e", SCRIPT], cwd=ROOT, input=json.dumps(ordered),
                            capture_output=True, text=True, check=True)
    js = json.loads(result.stdout)
    assert len(js) == len(ordered) == 359
    for lines, (blended, verdict, approver, gap, worst, js_lines) in zip(ordered, js, strict=True):
        py = guardrails.score_deal(lines)
        assert (py.verdict, py.approver, py.worst_line) == (verdict, approver, worst)
        assert py.blended_margin_pct == pytest.approx(blended, abs=1e-12)
        assert py.gap_dollars == pytest.approx(gap, abs=1e-9)
        for line, (margin, line_verdict, line_approver, line_gap) in zip(py.lines, js_lines, strict=True):
            assert (line.verdict, line.approver) == (line_verdict, line_approver)
            assert line.margin_pct == pytest.approx(margin, abs=1e-12)
            assert line.gap_dollars == pytest.approx(line_gap, abs=1e-9)
