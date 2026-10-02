"""Exercise the Salesforce build in a live org and record what happened in evidence/meridian/salesforce_live.json.

    python scripts/salesforce_live_checks.py --org crm-dev

1. Read: the REST API reads a loaded opportunity by its key and says whether its stored verdict is in step with a
   fresh score of its lines.
2. Async: the busiest product's target margin is lowered by 5 points. The Product2 trigger starts
   GuardrailRescoreBatch, which rescores every opportunity selling the product; each new verdict is checked against
   the Python guardrail given the same target. The target is then put back, and the batch puts every verdict back.
3. Sweep: the batch over every opportunity with lines, as the nightly schedule would run it, changes nothing.
4. Flow: a demo opportunity (no Crm_Platform_Key__c, so the loader never touches it) has a line repriced below its
   floor; the trigger moves the approver and the record-triggered Flow creates the signature task.

Steps 2 and 4 write to the org: 2 restores what it changed, 4 leaves its demo opportunity. Counts and IDs only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crm_platform import guardrails  # noqa: E402
from crm_platform.salesforce.load import API, EVIDENCE, SalesforceClient, cli_transport, org_guard  # noqa: E402
from crm_platform.salesforce.metadata import deal_lines  # noqa: E402
from crm_platform.tenants import TENANTS  # noqa: E402

GUARDRAIL = ("Meridian_Guardrail_Verdict__c", "Meridian_Approver__c", "Meridian_Blended_Margin__c",
             "Meridian_Margin_Gap_Dollars__c")
APEX_REST = "/services/apexrest/meridian/guardrail/v1"


def snapshot(client: SalesforceClient, where: str) -> dict[str, tuple[Any, ...]]:
    rows = client.query(f"SELECT Id, Crm_Platform_Key__c, {', '.join(GUARDRAIL)} FROM Opportunity WHERE {where}")
    return {row["Id"]: tuple(row[field] for field in ("Crm_Platform_Key__c", *GUARDRAIL)) for row in rows}


def latest_job(client: SalesforceClient) -> str:
    rows = client.query("SELECT CreatedDate FROM AsyncApexJob WHERE ApexClass.Name = 'GuardrailRescoreBatch' "
                        "AND JobType = 'BatchApex' ORDER BY CreatedDate DESC LIMIT 1")
    return rows[0]["CreatedDate"] if rows else ""


def wait_for_batch(client: SalesforceClient, after: str, timeout: float = 600) -> dict[str, Any]:
    """The first GuardrailRescoreBatch started after ``after``, once it has finished."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = client.query(
            "SELECT Id, Status, JobItemsProcessed, TotalJobItems, NumberOfErrors, CreatedDate, CompletedDate "
            "FROM AsyncApexJob WHERE ApexClass.Name = 'GuardrailRescoreBatch' AND JobType = 'BatchApex' "
            "ORDER BY CreatedDate DESC LIMIT 1")
        if rows and rows[0]["CreatedDate"] > after and rows[0]["Status"] in ("Completed", "Failed", "Aborted"):
            job = rows[0]
            return {"job": job["Id"], "status": job["Status"], "batches": job["TotalJobItems"],
                    "errors": job["NumberOfErrors"], "started": job["CreatedDate"], "finished": job["CompletedDate"]}
        time.sleep(5)
    raise SystemExit("GuardrailRescoreBatch did not finish in time")


def read_by_key(client: SalesforceClient) -> dict[str, Any]:
    key = client.query("SELECT Crm_Platform_Key__c FROM Opportunity WHERE Meridian_Approver__c = 'vp_finance' "
                       "AND Crm_Platform_Key__c != null ORDER BY Crm_Platform_Key__c LIMIT 1")[0]["Crm_Platform_Key__c"]
    answer = client.call("GET", f"{APEX_REST}/opportunities/{quote(key, safe='')}")
    expected = guardrails.score_deal(deal_lines(TENANTS["meridian"].records())[key])
    return {"key": key, "in_step": answer["inStep"], "stored_approver": answer["stored"]["approver"],
            "live_verdict": answer["live"]["verdict"], "python_verdict": expected.verdict,
            "unscored_lines": answer["unscoredLines"]}


def retarget(client: SalesforceClient) -> dict[str, Any]:
    busiest = client.query("SELECT Product2Id, COUNT(Id) lines FROM OpportunityLineItem GROUP BY Product2Id "
                           "ORDER BY COUNT(Id) DESC LIMIT 1")[0]
    product_id = busiest["Product2Id"]
    product = client.query(f"SELECT Crm_Platform_Key__c, Meridian_Target_Margin__c FROM Product2 "
                           f"WHERE Id = '{product_id}'")[0]
    original = product["Meridian_Target_Margin__c"]
    lowered = round(original - 0.05, 4)
    where = f"Id IN (SELECT OpportunityId FROM OpportunityLineItem WHERE Product2Id = '{product_id}')"
    before = snapshot(client, where)

    tasks_before = _tasks(client, list(before))
    started = latest_job(client)
    client.call("PATCH", f"{API}/sobjects/Product2/{product_id}", {"Meridian_Target_Margin__c": lowered})
    moved_job = wait_for_batch(client, started)
    moved = snapshot(client, where)

    # The Python guardrail, given the same lowered target, must reach the verdicts the batch wrote.
    records = TENANTS["meridian"].records()
    deals = deal_lines(records)
    key_of = product["Crm_Platform_Key__c"]
    lines_with_target = {
        deal: [(p, c, q, lowered if _sells(records, deal, i, key_of) else t)
               for i, (p, c, q, t) in enumerate(lines)]
        for deal, lines in deals.items()}
    disagree = []
    for row in moved.values():
        if row[0] is None:
            continue  # a demo opportunity from step 4 of an earlier run: not loaded, nothing to compare with
        score = guardrails.score_deal(lines_with_target[row[0]])
        if (row[1], row[2]) != (_option(score.verdict), _option(score.approver)):
            disagree.append(row[0])

    started = latest_job(client)
    client.call("PATCH", f"{API}/sobjects/Product2/{product_id}", {"Meridian_Target_Margin__c": original})
    restored_job = wait_for_batch(client, started)
    restored = snapshot(client, where)
    open_deals = len(client.query(f"SELECT Id FROM Opportunity WHERE IsClosed = false AND {where}"))
    return {"product": key_of, "open_opportunities": open_deals,
            "signature_tasks_the_flow_created": _tasks(client, list(before)) - tasks_before,
            "lines": busiest["lines"], "opportunities": len(before),
            "target_moved_by": -0.05,
            "after_lowering": {"batch": moved_job,
                               "verdicts_changed": sum(before[i][1:3] != moved[i][1:3] for i in before),
                               "fields_changed": sum(before[i] != moved[i] for i in before),
                               "disagree_with_python": disagree},
            "after_restoring": {"batch": restored_job, "identical_to_before": restored == before}}


def _tasks(client: SalesforceClient, opportunity_ids: list[str]) -> int:
    """Tasks on these opportunities (SOQL allows no semi-join inside a semi-join, so the IDs are listed)."""
    ids = ", ".join(f"'{i}'" for i in opportunity_ids)
    return len(client.query(f"SELECT Id FROM Task WHERE WhatId IN ({ids})")) if ids else 0


def _sells(records: list[Any], deal: str, index: int, product_key: str) -> bool:
    lines = [r for r in records if r.object_name == "line_items" and r.links[0].to_key == deal]
    return lines[index].properties["hs_product_id"].rsplit(":", 1)[1] == product_key


def _option(label: str) -> str:
    """GuardrailService.optionValue: "Below floor" -> "below_floor"."""
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def sweep(client: SalesforceClient) -> dict[str, Any]:
    where = "Crm_Platform_Key__c != null"
    before = snapshot(client, where)
    started = latest_job(client)
    apex = "Database.executeBatch(new GuardrailRescoreBatch(), GuardrailRescoreBatch.SCOPE);"
    ran = client.call("GET", f"{API}/tooling/executeAnonymous?anonymousBody={quote(apex)}")
    if not (ran.get("compiled") and ran.get("success")):
        raise SystemExit("the sweep did not start")
    job = wait_for_batch(client, started)
    after = snapshot(client, where)
    return {"batch": job, "opportunities": len(after), "changed": sum(before[i] != after.get(i) for i in before)}


def flow(client: SalesforceClient) -> dict[str, Any]:
    stage = client.query("SELECT ApiName FROM OpportunityStage WHERE IsActive = true AND IsClosed = false "
                         "ORDER BY SortOrder LIMIT 1")[0]["ApiName"]
    book = client.query("SELECT Id FROM Pricebook2 WHERE IsStandard = true")[0]["Id"]
    entry = client.query("SELECT Id, Product2.Meridian_Target_Margin__c FROM PricebookEntry "
                         "WHERE Pricebook2.IsStandard = true AND Product2.Meridian_Target_Margin__c != null "
                         "ORDER BY Product2.Crm_Platform_Key__c LIMIT 1")[0]
    target = entry["Product2"]["Meridian_Target_Margin__c"]
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
    opp = client.call("POST", f"{API}/sobjects/Opportunity", {
        "Name": f"Flow demo {stamp}: a line repriced below its floor", "StageName": stage,
        "CloseDate": (date.today() + timedelta(days=30)).isoformat(), "Pricebook2Id": book})["id"]
    cost = 50.0
    healthy = round(cost / (1 - (target + 0.10)), 2)  # above stretch: nobody needs to sign
    line = client.call("POST", f"{API}/sobjects/OpportunityLineItem", {
        "OpportunityId": opp, "PricebookEntryId": entry["Id"], "Quantity": 10, "UnitPrice": healthy,
        "Unit_Cost__c": cost})["id"]
    first = client.query(f"SELECT Meridian_Approver__c FROM Opportunity WHERE Id = '{opp}'")[0]
    tasks_before = len(client.query(f"SELECT Id FROM Task WHERE WhatId = '{opp}'"))
    client.call("PATCH", f"{API}/sobjects/OpportunityLineItem/{line}", {"UnitPrice": 52.0})  # 3.8%: below any floor
    second = client.query(f"SELECT Meridian_Guardrail_Verdict__c, Meridian_Approver__c FROM Opportunity "
                          f"WHERE Id = '{opp}'")[0]
    tasks = client.query(f"SELECT Subject, Priority, Status FROM Task WHERE WhatId = '{opp}'")
    return {"opportunity": opp, "approver_at_a_healthy_price": first["Meridian_Approver__c"],
            "tasks_at_a_healthy_price": tasks_before,
            "verdict_after_repricing": second["Meridian_Guardrail_Verdict__c"],
            "approver_after_repricing": second["Meridian_Approver__c"],
            "tasks_created": [{"subject": t["Subject"], "priority": t["Priority"], "status": t["Status"]}
                              for t in tasks]}


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--org", required=True)
    args = parser.parse_args()
    client = SalesforceClient(cli_transport(args.org))
    report: dict[str, Any] = {"tenant": "meridian", "org": org_guard(client, "meridian", binding=False),
              "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    report["read_by_key"] = read_by_key(client)
    report["retarget"] = retarget(client)
    report["sweep"] = sweep(client)
    report["flow"] = flow(client)
    report["requests"] = client.requests
    path = EVIDENCE / "meridian" / "salesforce_live.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
