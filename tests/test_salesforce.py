"""The Salesforce compilation of the same models: valid, conventional, and committed."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from crm_platform.salesforce.metadata import NS, OUT, build, problems
from crm_platform.tenants import TENANTS

LOADED = [k for k in sorted(TENANTS) if TENANTS[k].model().objects]


@pytest.mark.parametrize("key", LOADED)
def test_generated_metadata_is_valid_and_committed(key):
    files = build(TENANTS[key].model())
    assert problems(files) == []
    for rel, text in files.items():
        assert (OUT / key / rel).read_text(encoding="utf-8") == text, f"stale: {rel}"


def _field(key: str, obj: str, name: str) -> ET.Element:
    files = build(TENANTS[key].model())
    return ET.fromstring(files[f"force-app/main/default/objects/{obj}/fields/{name}.field-meta.xml"])


def test_the_key_is_an_external_id_unique_where_the_model_says():
    account_key = _field("meridian", "Account", "Crm_Platform_Key__c")
    assert account_key.findtext(f"{{{NS}}}externalId") == "true"
    assert account_key.findtext(f"{{{NS}}}unique") == "true"
    line_key = _field("meridian", "OpportunityLineItem", "Crm_Platform_Key__c")
    assert line_key.findtext(f"{{{NS}}}unique") == "false"


def test_types_map_to_salesforce_field_types():
    assert _field("aml", "Investigation_Case__c", "Rules_Fired__c").findtext(f"{{{NS}}}type") == "MultiselectPicklist"
    assert _field("aml", "Investigation_Case__c", "Evidence_References__c").findtext(f"{{{NS}}}type") == "LongTextArea"
    assert _field("aml", "Investigation_Case__c", "Sla_Started_At__c").findtext(f"{{{NS}}}type") == "DateTime"
    priority = _field("aml", "Investigation_Case__c", "Case_Priority__c")
    assert priority.findtext(f"{{{NS}}}type") == "Picklist"
    assert [v.findtext(f"{{{NS}}}label") for v in priority.iter(f"{{{NS}}}value")] == ["P0", "P1", "P2"]


def test_associations_become_lookups_junctions_and_roles():
    files = build(TENANTS["aml"].model())
    subject = ET.fromstring(files["force-app/main/default/objects/Investigation_Case__c/fields/"
                                  "Subject_Contact__c.field-meta.xml"])
    assert (subject.findtext(f"{{{NS}}}type"), subject.findtext(f"{{{NS}}}referenceTo")) == ("Lookup", "Contact")
    junction = [p for p in files if "/Transacted_With__c/fields/" in p]
    assert len(junction) == 2
    assert all(ET.fromstring(files[p]).findtext(f"{{{NS}}}type") == "MasterDetail" for p in junction)
    assert "Buyer" in build(TENANTS["meridian"].model())[
        "force-app/main/default/standardValueSets/RoleOnAccountContact.notes.md"]


def test_the_deal_pipeline_is_a_sales_process_and_custom_pipelines_a_stage_picklist():
    meridian = build(TENANTS["meridian"].model())
    stages = ET.fromstring(meridian["force-app/main/default/standardValueSets/"
                                    "OpportunityStage.standardValueSet-meta.xml"])
    won = next(v for v in stages.iter(f"{{{NS}}}standardValue") if v.findtext(f"{{{NS}}}fullName") == "Won")
    assert (won.findtext(f"{{{NS}}}closed"), won.findtext(f"{{{NS}}}won")) == ("true", "true")
    assert any(p.endswith("Quote_To_Order.businessProcess-meta.xml") for p in meridian)
    stage = _field("aml", "Investigation_Case__c", "Stage__c")
    assert "Closed: no further action" in stage.findtext(f"{{{NS}}}description")


def test_the_primary_display_property_is_the_name_field_not_a_custom_field():
    files = build(TENANTS["crosssell"].model())
    assert not any(p.endswith("Offer_Title__c.field-meta.xml") for p in files)
    obj = ET.fromstring(files["force-app/main/default/objects/Recommendation__c/Recommendation__c.object-meta.xml"])
    assert obj.find(f"{{{NS}}}nameField/{{{NS}}}label").text == "Offer"


def test_package_manifest_lists_every_component():
    files = build(TENANTS["crosssell"].model())
    package = ET.fromstring(files["manifest/package.xml"])
    members = {m.text for m in package.iter(f"{{{NS}}}members")}
    fields = {Path(p).name.removesuffix(".field-meta.xml") for p in files if p.endswith(".field-meta.xml")}
    assert {m.split(".")[1] for m in members if "." in m} >= fields
