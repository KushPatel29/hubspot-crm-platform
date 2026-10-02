"""The Salesforce loader: the same promise as the HubSpot one (keyed, rerunnable to zero writes, never deleting),
against an in-memory Salesforce that owns an opportunity's Amount the way the real one does."""

from __future__ import annotations

import json

import pytest
from fake_salesforce import FakeSalesforce

from crm_platform.salesforce import load as sf
from crm_platform.tenants import TENANTS


@pytest.fixture
def fake() -> FakeSalesforce:
    return FakeSalesforce()


@pytest.fixture
def meridian():
    tenant = TENANTS["meridian"]
    return tenant.model(), tenant.records()


def _client(fake: FakeSalesforce) -> sf.SalesforceClient:
    return sf.SalesforceClient(fake, sleep=lambda _: None)


def test_a_plan_counts_what_it_would_create_and_writes_nothing(fake, meridian):
    model, records = meridian
    client = _client(fake)
    report = sf.load(client, model, records, write=False)
    assert {name: plan["create"] for name, plan in report["objects"].items()} == {
        "companies": 150, "contacts": 150, "products": 240, "deals": 359, "line_items": 480}
    assert client.writes == 0 and not sf.converged(report)


def test_a_load_then_a_rerun_writes_nothing(fake, meridian):
    model, records = meridian
    first = sf.load(_client(fake), model, records, write=True)
    assert not first["failures"]
    assert first["price_book"]["create"] == 240 and fake.book["IsActive"] is True
    again = _client(fake)
    second = sf.load(again, model, records, write=True)
    assert sf.converged(second) and again.writes == 0
    assert all(plan["unchanged"] == plan["desired"] for plan in second["objects"].values())


def test_records_land_on_their_parents_stages_by_label_and_numbers_as_numbers(fake, meridian):
    model, records = meridian
    sf.load(_client(fake), model, records, write=True)
    deal = next(r for r in records if r.object_name == "deals")
    opportunity = fake.by_key("Opportunity", deal.key)
    assert opportunity["StageName"] in ("Won", "Lost") and opportunity["CloseDate"] == deal.properties["closedate"]
    assert opportunity["AccountId"] == fake.by_key("Account", deal.links[0].to_key)["Id"]
    assert opportunity["Pricebook2Id"] == fake.book["Id"]
    assert isinstance(opportunity["Meridian_Blended_Margin__c"], float)
    line_record = next(r for r in records if r.object_name == "line_items" and r.links[0].to_key == deal.key)
    line = fake.by_key("OpportunityLineItem", line_record.key)
    product_key = line_record.properties["hs_product_id"].rsplit(":", 1)[1]
    entry = fake.objects["PricebookEntry"][line["PricebookEntryId"]]
    assert line["OpportunityId"] == opportunity["Id"]
    assert entry["Product2Id"] == fake.by_key("Product2", product_key)["Id"]
    assert line["Unit_Cost__c"] == float(line_record.properties["hs_cost_of_goods_sold"])
    assert fake.by_key("Product2", product_key)["IsActive"] is True
    contact = fake.by_key("Contact", f"{deal.links[0].to_key}-buyer")
    assert contact["AccountId"] == opportunity["AccountId"]


def test_a_changed_source_value_updates_only_that_field(fake, meridian):
    model, records = meridian
    sf.load(_client(fake), model, records, write=True)
    account = fake.by_key("Account", "CU2000")
    account["Meridian_Price_Sensitivity__c"] = 1.0
    report = sf.load(_client(fake), model, records, write=False)
    assert report["objects"]["companies"]["update"] == 1
    assert report["objects"]["companies"]["fields_changed"] == ["Meridian_Price_Sensitivity__c"]


def test_the_opportunity_amount_is_left_to_salesforce_once_the_lines_exist(fake, meridian):
    model, records = meridian
    sf.load(_client(fake), model, records, write=True)
    for opportunity in fake.records("Opportunity"):
        opportunity["Amount"] = (opportunity["Amount"] or 0) + 0.03  # Salesforce's own rounding of the line totals
    client = _client(fake)
    report = sf.load(client, model, records, write=True)
    assert report["objects"]["deals"]["update"] == 0 and client.writes == 0 and not report["failures"]


def test_a_refused_record_is_reported_by_code_and_field_never_by_message(fake, meridian):
    model, records = meridian
    broken = [r for r in records if r.object_name != "deals"]  # the lines' opportunities never arrive
    report = sf.load(_client(fake), model, broken, write=True)
    failures = [f for f in report["failures"] if f["object"] == "OpportunityLineItem"]
    assert len(failures) == 480
    assert failures[0] == {"object": "OpportunityLineItem", "action": "create", "codes": ["REQUIRED_FIELD_MISSING"],
                           "fields": ["OpportunityId"]}
    assert "Required fields are missing" not in json.dumps(report)
    assert not sf.converged(report)


def test_only_developer_orgs_and_sandboxes_are_loaded_and_a_tenant_stays_on_its_org(monkeypatch, tmp_path, fake):
    monkeypatch.setattr(sf, "EVIDENCE", tmp_path)
    with pytest.raises(SystemExit, match="Developer Edition or a sandbox only"):
        sf.org_guard(_client(FakeSalesforce("Enterprise Edition")), "meridian", binding=True)
    assert sf.org_guard(_client(fake), "meridian", binding=True)["org_id"] == "00D000000000001"
    assert json.loads((tmp_path / "meridian" / "salesforce_org.json").read_text())["orgId"] == "00D000000000001"
    with pytest.raises(SystemExit, match="bound to Salesforce org 00D000000000001"):
        sf.org_guard(_client(FakeSalesforce(org_id="00D000000000999")), "meridian", binding=True)


def test_transient_failures_are_retried_and_errors_never_carry_the_message():
    answers = [(503, None, {}), (200, {"done": True, "records": [{"Id": "1"}]}, {})]
    waits: list[float] = []
    client = sf.SalesforceClient(lambda *_: answers.pop(0), sleep=waits.append)
    assert client.query("SELECT Id FROM Account") == [{"Id": "1"}] and waits == [0.5]
    refused = sf.SalesforceClient(lambda *_: (400, [{"errorCode": "MALFORMED_QUERY", "message": "jane@example.com"}],
                                              {}), sleep=waits.append)
    with pytest.raises(sf.SalesforceError, match="MALFORMED_QUERY") as caught:
        refused.query("SELECT")
    assert "jane@example.com" not in str(caught.value)


def test_the_session_token_comes_from_org_auth_when_org_display_hides_it():
    asked = []

    def new_cli(args):
        asked.append(args[:2] if args[0] == "org" and args[1] == "display" else args[:3])
        if args[1] == "display":
            return {"accessToken": "[REDACTED] Use 'sf org auth show-access-token' to view",
                    "instanceUrl": "https://example.my.salesforce.com"}
        return {"accessToken": "00Dxx!session"}

    assert sf.cli_session(new_cli, "crm-dev") == ("00Dxx!session", "https://example.my.salesforce.com")
    assert asked == [["org", "display"], ["org", "auth", "show-access-token"]]

    # An older CLI still answers with the token itself, and is asked once.
    asked.clear()

    def old_cli(args):
        asked.append(args[:2])
        return {"accessToken": "00Dxx!older", "instanceUrl": "https://example.my.salesforce.com"}

    assert sf.cli_session(old_cli, "crm-dev")[0] == "00Dxx!older"
    assert asked == [["org", "display"]]
