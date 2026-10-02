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


def test_unit_cost_is_a_currency_field_wherever_hubspot_holds_cost_of_goods():
    for obj in ("Product2", "OpportunityLineItem"):
        cost = _field("meridian", obj, "Unit_Cost__c")
        assert (cost.findtext(f"{{{NS}}}type"), cost.findtext(f"{{{NS}}}scale")) == ("Currency", "2")
    assert "force-app/main/default/objects/Product2/fields/Unit_Cost__c.field-meta.xml" in build(
        TENANTS["crosssell"].model())


def test_the_permission_set_grants_every_field_except_the_ones_salesforce_refuses():
    files = build(TENANTS["aml"].model())
    name = "force-app/main/default/permissionsets/Crm_Platform_Aml.permissionset-meta.xml"
    granted = {f.findtext(f"{{{NS}}}field") for f in ET.fromstring(files[name]).iter(f"{{{NS}}}fieldPermissions")}
    assert "Investigation_Case__c.Case_Priority__c" in granted and "Contact.Crm_Platform_Key__c" in granted
    assert "Investigation_Case__c.Stage__c" not in granted  # required: access comes from the object
    assert not any(field.startswith("Transacted_With__c.") for field in granted)  # master-detail
    objects = {o.findtext(f"{{{NS}}}object") for o in ET.fromstring(files[name]).iter(f"{{{NS}}}objectPermissions")}
    assert {"Investigation_Case__c", "Counterparty__c", "Transacted_With__c"} <= objects
    package = ET.fromstring(files["manifest/package.xml"])
    assert "Crm_Platform_Aml" in {m.text for m in package.iter(f"{{{NS}}}members")}


def test_hand_written_code_is_a_second_package_directory_the_generator_never_owns():
    import json

    from crm_platform.salesforce.metadata import GENERATED_DIRS, HAND_WRITTEN

    for key in LOADED:
        files = build(TENANTS[key].model())
        directories = [d["path"] for d in json.loads(files["sfdx-project.json"])["packageDirectories"]]
        assert directories == (["force-app", "code"] if key in HAND_WRITTEN else ["force-app"])
        assert all(rel.split("/")[0] in (*GENERATED_DIRS, "sfdx-project.json", ".forceignore") for rel in files)
        assert "**/__tests__/**" in files[".forceignore"].splitlines()  # a deploy must not pick up the Jest tests
    code = OUT / "meridian" / "code" / "main" / "default"
    assert (code / "classes" / "GuardrailService.cls").exists() and (code / "lwc" / "dealMarginGuardrail").is_dir()


def test_the_apex_parity_fixture_is_every_meridian_deal_scored_by_the_python_guardrail():
    import json

    from crm_platform import guardrails
    from crm_platform.salesforce.metadata import PARITY, parity_fixture

    files = parity_fixture(TENANTS["meridian"].records())
    assert (OUT / "meridian" / f"{PARITY}.json").read_text(encoding="utf-8") == files[f"{PARITY}.json"], "stale"
    cases = json.loads(files[f"{PARITY}.json"])
    assert len(cases) == 359
    for case in cases[:25]:
        score = guardrails.score_deal([tuple(line) for line in case["lines"]])
        assert (score.verdict, score.approver, score.worst_line) == (case["verdict"], case["approver"],
                                                                     case["worstLine"])
        assert score.blended_margin_pct == case["blendedMarginPct"]  # JSON round-trips a double exactly
    assert ET.fromstring(files[f"{PARITY}.resource-meta.xml"]).findtext(f"{{{NS}}}contentType") == "application/json"


def test_the_guardrail_settings_record_holds_the_python_rule():
    """Salesforce reads the band and the tiers from custom metadata; the record that ships must be the Python rule."""
    from crm_platform import guardrails

    path = OUT / "meridian" / "code" / "main" / "default" / "customMetadata"
    record = ET.parse(path / "Meridian_Guardrail_Setting.Default.md-meta.xml").getroot()
    values = {v.findtext(f"{{{NS}}}field"): float(v.findtext(f"{{{NS}}}value") or "nan")
              for v in record.iter(f"{{{NS}}}values")}
    limits = [limit for limit, _ in guardrails.APPROVAL_TIERS[:-1]]
    assert values == {"Floor_Minimum__c": guardrails.FLOOR_MINIMUM, "Floor_Drop__c": guardrails.FLOOR_DROP,
                      "Stretch_Rise__c": guardrails.STRETCH_RISE, "Rep_Limit__c": limits[0],
                      "Manager_Limit__c": limits[1], "Director_Limit__c": limits[2]}
    fields = {f.stem.split(".")[0] for f in (path.parent / "objects" / "Meridian_Guardrail_Setting__mdt" / "fields")
              .glob("*.field-meta.xml")}
    assert fields == set(values), "every setting has a field and every field a value"

