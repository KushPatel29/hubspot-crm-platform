"""The records each business loads: complete, consistent with the model, and faithful to the source project."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from crm_platform import guardrails
from crm_platform.model import PRIMARY
from crm_platform.snapshots import SNAPSHOTS, rows
from crm_platform.tenants import TENANTS

ROOT = Path(__file__).resolve().parents[1]
STANDARD_WRITES = {
    "companies": {"name", "domain"}, "contacts": {"email", "firstname", "lastname", "jobtitle"},
    "deals": {"dealname", "amount", "pipeline", "dealstage", "closedate"},
    "products": {"name", "hs_sku", "price", "hs_cost_of_goods_sold", "description"},
    "line_items": {"name", "quantity", "price", "hs_cost_of_goods_sold", "hs_product_id"},
    "investigation_case": {"hs_pipeline", "hs_pipeline_stage"},
}
LOADED = [k for k in sorted(TENANTS) if TENANTS[k].model().objects]


def test_committed_snapshots_match_their_manifest():
    manifest = json.loads((SNAPSHOTS / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["tenants"]) == {"meridian", "crosssell", "aml"}
    for tenant in manifest["tenants"].values():
        assert len(tenant["commit"]) == 40
        for entry in tenant["files"]:
            text = (ROOT / entry["file"]).read_text(encoding="utf-8")
            assert hashlib.sha256(text.encode("utf-8")).hexdigest() == entry["sha256"], entry["file"]
            assert text.count("\n") - 1 == entry["rows"], entry["file"]
            assert entry["sources"] and entry["rule"]


@pytest.mark.parametrize("key", LOADED)
def test_records_write_only_declared_or_standard_properties_with_valid_values(key):
    model, records = TENANTS[key].model(), TENANTS[key].records()
    for record in records:
        obj = model.object(record.object_name)
        assert record.key and record.properties
        for name, value in record.properties.items():
            if name in obj.names:
                prop = obj.prop(name)
                if prop.type == "enumeration" and value:
                    allowed = {o.value for o in prop.options}
                    parts = value.split(";") if prop.field_type == "checkbox" else [value]
                    assert set(parts) <= allowed, (record.object_name, record.key, name, value)
            else:
                assert name in STANDARD_WRITES.get(record.object_name, set()), (record.object_name, name)
        assert set(record.create_only) <= set(record.properties)


@pytest.mark.parametrize("key", LOADED)
def test_keys_are_unique_and_every_link_and_reference_lands_on_a_loaded_record(key):
    model, records = TENANTS[key].model(), TENANTS[key].records()
    keys = Counter((r.object_name, r.key) for r in records)
    assert max(keys.values()) == 1
    labels = {(a.from_object, a.to_object, a.name) for a in model.associations}
    for record in records:
        for link in record.links:
            assert (link.to_object, link.to_key) in keys, (record.key, link)
            assert link.label in ("", PRIMARY) or (record.object_name, link.to_object, link.label) in labels
        for value in record.properties.values():
            if value.startswith("@record:"):
                _, object_name, ref = value.split(":", 2)
                assert (object_name, ref) in keys


def test_meridian_deals_are_the_quote_book_split_by_outcome_with_amounts_equal_to_their_lines():
    records = TENANTS["meridian"].records()
    deals = {r.key: r for r in records if r.object_name == "deals"}
    lines = defaultdict(list)
    for r in records:
        if r.object_name == "line_items":
            lines[r.links[0].to_key].append(r)
    quotes = rows("meridian", "quotes.csv")
    assert sum(len(v) for v in lines.values()) == len(quotes) == 480
    for key, deal in deals.items():
        total = sum(float(li.properties["price"]) * float(li.properties["quantity"]) for li in lines[key])
        assert abs(total - float(deal.properties["amount"])) < 0.006, key
        outcomes = {li.properties["meridian_line_outcome"] for li in lines[key]}
        assert outcomes == {key.rsplit("-", 1)[1]}
        assert deal.properties["dealstage"].endswith(":Won" if outcomes == {"won"} else ":Lost")
    split = Counter(k.rsplit("-", 1)[0] for k in deals)
    assert sum(1 for n in split.values() if n == 2) == 81  # partly won requests, closed as a won and a lost deal
    assert len(deals) == 359


def test_meridian_deal_guardrail_is_the_worst_line_and_the_most_senior_signature():
    records = TENANTS["meridian"].records()
    deal = next(r for r in records if r.object_name == "deals" and r.properties["meridian_approver"] == "vp_finance")
    lines = [r for r in records if r.object_name == "line_items" and r.links[0].to_key == deal.key]
    verdicts = [li.properties["meridian_line_verdict"] for li in lines]
    order = [v.lower().replace(" ", "_").replace("-", "_") for v in guardrails.VERDICTS]
    assert deal.properties["meridian_guardrail_verdict"] == max(verdicts, key=order.index)


def test_crosssell_offers_are_eligible_top_three_per_account_and_open_once():
    records = TENANTS["crosssell"].records()
    offers = [r for r in records if r.object_name == "recommendation"]
    per_account = Counter(r.links[0].to_key for r in offers)
    assert len(offers) == 302 and max(per_account.values()) <= 3
    assert all(r.properties["offer_eligibility"] == "SERVE" for r in offers)
    assert all(r.properties["offer_status"] == "open" and "offer_status" in r.create_only for r in offers)


def test_aml_cases_keep_the_investigators_fields_create_only_and_link_their_subject():
    records = TENANTS["aml"].records()
    cases = [r for r in records if r.object_name == "investigation_case"]
    assert len(cases) == 366
    for case in cases:
        assert {"hs_pipeline_stage", "decision_status", "sla_started_at"} <= case.create_only
        subject = case.links[0]
        assert subject.label == "subject"
        assert subject.to_object == ("contacts" if case.properties["subject_type"] == "individual" else "companies")
    counterparties = [r for r in records if r.object_name == "counterparty"]
    assert len(counterparties) == 286 and min(int(r.properties["hot_subjects"]) for r in counterparties) >= 2
    assert all(r.properties["filing_status"] == "not_assessed_or_filed" for r in cases)


def test_synthetic_contacts_are_labelled_and_use_reserved_domains():
    for key in ("meridian", "crosssell"):
        for record in TENANTS[key].records():
            if record.object_name == "contacts":
                assert "synthetic" in record.properties["jobtitle"]
                assert record.properties["email"].endswith(".example.com")


def test_a_rounding_tie_goes_the_way_javascript_and_apex_send_it():
    from crm_platform.snapshots import money, number

    # Exact in binary, so a true tie: toFixed and HALF_UP round it up, an f-string would round it to even.
    assert number(0.53125) == "0.5313"
    assert number(-0.53125) == "-0.5313"
    assert money(20.625) == "20.63"
    # Not a tie: 2.675 is stored a little below, and every language says 2.67.
    assert money(2.675) == "2.67"
    assert (number(100, 0), number(0), number(-0.00001), number("3.10")) == ("100", "0", "0", "3.1")
    # The one Meridian deal that is a tie carries the value the HubSpot function and the Apex trigger write.
    deals = {r.key: r for r in TENANTS["meridian"].records() if r.object_name == "deals"}
    assert deals["RFQ-CU2140-2026-06-won"].properties["meridian_blended_margin"] == "0.5313"
