"""The same tenant models compiled to Salesforce metadata (SFDX source format), so a Salesforce build is a deploy away.

The output is generated, parsed back as XML and checked against Salesforce's naming rules by the tests, and
committed under ``salesforce/<tenant>/force-app`` with a CI gate that fails if it drifts from the models. A tenant
may also carry hand-written Salesforce code (Apex, Lightning Web Components, flows) under ``salesforce/<tenant>/code``,
a second package directory this generator lists but never writes to or deletes from, except the guardrail parity
fixture it derives from the same records the Python and JavaScript guardrails are tested on. What maps to what:

========================  ==============================================================================
HubSpot (the model)       Salesforce
========================  ==============================================================================
contacts / companies      Contact / Account
deals / line_items        Opportunity / OpportunityLineItem
products                  Product2
custom object             ``<Name>__c``; its primary display property becomes the record Name field
property                  custom field ``<Title_Case>__c``: text → Text(255), textarea → LongTextArea,
                          number → Number(18, 4), date / datetime → Date / DateTime, select / radio →
                          restricted Picklist, checkbox → MultiselectPicklist, booleancheckbox → Checkbox
crm_platform_key          Text external ID, unique where the model marks it unique (upsert by it)
hs_cost_of_goods_sold     ``Unit_Cost__c`` Currency(16, 2) on Product2 and OpportunityLineItem (HubSpot's own
                          property, which Salesforce has no standard field for)
field access              a permission set, ``Crm_Platform_<Tenant>``, granting read/edit on every field above
deal pipeline             an Opportunity sales process (BusinessProcess) over the OpportunityStage values
custom-object pipeline    a restricted ``Stage__c`` picklist; closed stages named in its description
custom → standard label   a lookup field on the custom object (``Subject_Contact__c`` → Contact)
custom ↔ custom label     a junction object with two master-detail fields
contact → company label   an AccountContactRelation role (contacts to multiple accounts)
========================  ==============================================================================
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from crm_platform.model import KEY_PROPERTY, AssociationLabel, ObjectModel, Pipeline, Property, TenantModel
from crm_platform.tenants import TENANTS

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "salesforce"
NS = "http://soap.sforce.com/2006/04/metadata"
API_VERSION = "62.0"
STANDARD = {"contacts": "Contact", "companies": "Account", "deals": "Opportunity", "products": "Product2",
            "line_items": "OpportunityLineItem", "tickets": "Case"}
API_NAME = re.compile(r"^[A-Za-z](?:[A-Za-z0-9]|_(?!_))*(?<!_)$")
# Tenants with hand-written Salesforce code in salesforce/<tenant>/code (a second package directory).
HAND_WRITTEN = frozenset({"meridian"})
GENERATED_DIRS = ("force-app", "manifest")
UNIT_COST = "Unit_Cost__c"  # HubSpot's hs_cost_of_goods_sold, on products and line items
PARITY = "code/main/default/staticresources/GuardrailParity"


def api_name(name: str) -> str:
    """``"crm_platform_key"`` → ``"Crm_Platform_Key"`` (before any ``__c``)."""
    return "_".join(part[:1].upper() + part[1:] for part in name.split("_"))


def object_api(model: TenantModel, name: str) -> str:
    return STANDARD.get(name) or f"{api_name(model.object(name).name)}__c"


def _xml(root: str, body: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<{root} xmlns="{NS}">\n{body}</{root}>\n'


def _tag(name: str, value: object, indent: int = 1) -> str:
    text = str(value).lower() if isinstance(value, bool) else escape(str(value))
    return f"{'    ' * indent}<{name}>{text}</{name}>\n"


def _values(prop: Property) -> str:
    body = "".join("            <value>\n" + _tag("fullName", o.value, 4) + _tag("default", False, 4)
                   + _tag("label", o.label, 4) + "            </value>\n" for o in prop.options)
    return ("    <valueSet>\n" + _tag("restricted", True, 2) + "        <valueSetDefinition>\n"
            + _tag("sorted", False, 3) + body + "        </valueSetDefinition>\n    </valueSet>\n")


def field_xml(prop: Property) -> str:
    body = _tag("fullName", f"{api_name(prop.name)}__c") + _tag("label", prop.label[:40])
    if prop.description:
        body += _tag("description", prop.description[:1000]) + _tag("inlineHelpText", prop.description[:510])
    if prop.type == "string" and prop.field_type == "textarea":
        body += _tag("length", 32768) + _tag("type", "LongTextArea") + _tag("visibleLines", 5)
    elif prop.type == "string":
        body += _tag("length", 255) + _tag("required", False) + _tag("type", "Text")
        if prop.name == KEY_PROPERTY:
            body += _tag("externalId", True) + _tag("unique", prop.unique) + _tag("caseSensitive", False)
    elif prop.type == "number":
        body += _tag("precision", 18) + _tag("required", False) + _tag("scale", 4) + _tag("type", "Number")
    elif prop.type == "date":
        body += _tag("required", False) + _tag("type", "Date")
    elif prop.type == "datetime":
        body += _tag("required", False) + _tag("type", "DateTime")
    elif prop.type == "bool":
        body += _tag("defaultValue", False) + _tag("type", "Checkbox")
    elif prop.field_type == "checkbox":
        body += _tag("type", "MultiselectPicklist") + _values(prop) + _tag("visibleLines", 4)
    else:
        body += _tag("required", False) + _tag("type", "Picklist") + _values(prop)
    return _xml("CustomField", body)


def unit_cost_xml() -> str:
    return _xml("CustomField", _tag("fullName", UNIT_COST) + _tag("description", "HubSpot: hs_cost_of_goods_sold.")
                + _tag("label", "Unit cost") + _tag("precision", 16) + _tag("required", False) + _tag("scale", 2)
                + _tag("type", "Currency"))


def permission_set_name(model: TenantModel) -> str:
    return f"Crm_Platform_{api_name(model.key)}"


def permission_set_xml(model: TenantModel, fields: list[str], custom_objects: list[str]) -> str:
    """Read and edit on every generated field (a deploy grants no field access), and full access to the custom
    objects. Required and master-detail fields take their access from the object, so Salesforce refuses them here."""
    body = _tag("description", f"Field and object access for the CRM platform's {model.name} model. Generated.")
    body += _tag("hasActivationRequired", False) + _tag("label", f"CRM platform: {model.name}"[:80])
    for field in sorted(fields):
        body += ("    <fieldPermissions>\n" + _tag("editable", True, 2) + _tag("field", field, 2)
                 + _tag("readable", True, 2) + "    </fieldPermissions>\n")
    for obj in sorted(custom_objects):
        body += ("    <objectPermissions>\n" + _tag("allowCreate", True, 2) + _tag("allowDelete", True, 2)
                 + _tag("allowEdit", True, 2) + _tag("allowRead", True, 2) + _tag("modifyAllRecords", False, 2)
                 + _tag("object", obj, 2) + _tag("viewAllRecords", False, 2) + "    </objectPermissions>\n")
    return _xml("PermissionSet", body)


def parity_fixture(records: list) -> dict[str, str]:
    """Every Meridian deal's lines and the Python guardrail's answer for each, for the Apex test that holds the
    Salesforce guardrail to the same rule (a static resource in the hand-written code directory)."""
    from crm_platform import guardrails

    targets = {r.key: float(r.properties["meridian_target_margin"]) for r in records if r.object_name == "products"}
    deals: dict[str, list[tuple[float, float, float, float]]] = {}
    for r in records:
        if r.object_name == "line_items":
            product = r.properties["hs_product_id"].rsplit(":", 1)[1]
            deals.setdefault(r.links[0].to_key, []).append((
                float(r.properties["price"]), float(r.properties["hs_cost_of_goods_sold"]),
                float(r.properties["quantity"]), targets[product]))
    cases = []
    for key in sorted(deals):
        score = guardrails.score_deal(deals[key])
        cases.append({"deal": key, "lines": [list(line) for line in deals[key]], "verdict": score.verdict,
                      "approver": score.approver, "worstLine": score.worst_line,
                      "blendedMarginPct": score.blended_margin_pct, "gapDollars": score.gap_dollars,
                      "lineVerdicts": [line.verdict for line in score.lines],
                      "lineApprovers": [line.approver for line in score.lines]})
    meta = _xml("StaticResource", _tag("cacheControl", "Private") + _tag("contentType", "application/json")
                + _tag("description", "Generated: every Meridian deal and the Python guardrail's answer."))
    return {f"{PARITY}.json": json.dumps(cases, separators=(",", ":")) + "\n",
            f"{PARITY}.resource-meta.xml": meta}


def custom_object_xml(obj: ObjectModel, description: str) -> str:
    name_label = obj.prop(obj.primary_display).label
    body = (_tag("deploymentStatus", "Deployed") + _tag("description", description) + _tag("enableActivities", True)
            + _tag("enableHistory", True) + _tag("enableReports", True) + _tag("enableSearch", True)
            + _tag("label", obj.singular) + "    <nameField>\n" + _tag("label", name_label, 2)
            + _tag("type", "Text", 2) + "    </nameField>\n" + _tag("pluralLabel", obj.plural)
            + _tag("sharingModel", "ReadWrite"))
    return _xml("CustomObject", body)


def lookup_xml(field: str, label: str, target: str, relationship: str, related_label: str) -> str:
    return _xml("CustomField", _tag("fullName", field) + _tag("deleteConstraint", "SetNull") + _tag("label", label)
                + _tag("referenceTo", target) + _tag("relationshipLabel", related_label)
                + _tag("relationshipName", relationship) + _tag("required", False) + _tag("type", "Lookup"))


def master_detail_xml(field: str, label: str, target: str, relationship: str, related_label: str, order: int) -> str:
    return _xml("CustomField", _tag("fullName", field) + _tag("label", label) + _tag("referenceTo", target)
                + _tag("relationshipLabel", related_label) + _tag("relationshipName", relationship)
                + _tag("relationshipOrder", order) + _tag("reparentableMasterDetail", False)
                + _tag("type", "MasterDetail") + _tag("writeRequiresMasterRead", False))


def stage_field_xml(pipeline: Pipeline) -> str:
    closed = ", ".join(s.label for s in pipeline.stages if s.closed)
    values = "".join("            <value>\n" + _tag("fullName", api_name(re.sub(r"[^a-z0-9]+", "_", s.label.lower())
                                                                          .strip("_")), 4)
                     + _tag("default", i == 0, 4) + _tag("label", s.label, 4) + "            </value>\n"
                     for i, s in enumerate(pipeline.stages))
    return _xml("CustomField", _tag("fullName", "Stage__c") + _tag("description", f"{pipeline.label} pipeline. "
                                                                                 f"Closed stages: {closed}.")
                + _tag("label", "Stage") + _tag("required", True) + _tag("type", "Picklist")
                + "    <valueSet>\n" + _tag("restricted", True, 2) + "        <valueSetDefinition>\n"
                + _tag("sorted", False, 3) + values + "        </valueSetDefinition>\n    </valueSet>\n")


def business_process_xml(pipeline: Pipeline) -> str:
    values = "".join("    <values>\n" + _tag("fullName", s.label, 2) + _tag("default", i == 0, 2) + "    </values>\n"
                     for i, s in enumerate(pipeline.stages))
    return _xml("BusinessProcess", _tag("fullName", api_name(re.sub(r"[^a-z0-9]+", "_", pipeline.label.lower())
                                                               .strip("_")))
                + _tag("isActive", True) + values)


def opportunity_stages_xml(pipeline: Pipeline) -> str:
    values = "".join(
        "    <standardValue>\n" + _tag("fullName", s.label, 2) + _tag("default", False, 2) + _tag("label", s.label, 2)
        + _tag("closed", s.closed, 2) + _tag("won", s.closed and (s.probability or 0) >= 1, 2)
        + _tag("probability", int(round((s.probability or 0) * 100)), 2)
        + _tag("forecastCategory", "Closed" if s.closed and (s.probability or 0) >= 1
               else "Omitted" if s.closed else "Pipeline", 2)
        + "    </standardValue>\n" for s in pipeline.stages)
    return _xml("StandardValueSet", _tag("sorted", False) + values)


def build(model: TenantModel) -> dict[str, str]:
    """Every file for one tenant, keyed by path relative to ``salesforce/<tenant>``."""
    base = "force-app/main/default/objects"
    files: dict[str, str] = {}
    members: dict[str, list[str]] = {"CustomObject": [], "CustomField": [], "BusinessProcess": [],
                                     "StandardValueSet": [], "PermissionSet": []}
    grantable: list[str] = []  # fields a permission set may grant (not required, not master-detail)

    def add_field(obj_api: str, name: str, xml: str) -> None:
        files[f"{base}/{obj_api}/fields/{name}.field-meta.xml"] = xml
        members["CustomField"].append(f"{obj_api}.{name}")
        if "<type>MasterDetail</type>" not in xml and "<required>true</required>" not in xml:
            grantable.append(f"{obj_api}.{name}")

    for obj in model.objects:
        obj_api = object_api(model, obj.name)
        if obj.custom:
            files[f"{base}/{obj_api}/{obj_api}.object-meta.xml"] = custom_object_xml(obj, model.description)
            members["CustomObject"].append(obj_api)
        for prop in obj.properties:
            if obj.custom and prop.name == obj.primary_display:
                continue  # the record Name field
            add_field(obj_api, f"{api_name(prop.name)}__c", field_xml(prop))
        if obj.name in ("products", "line_items"):
            add_field(obj_api, UNIT_COST, unit_cost_xml())

    for pipeline in model.pipelines:
        if pipeline.object_name == "deals":
            process = business_process_xml(pipeline)
            name = re.search(r"<fullName>([^<]+)</fullName>", process)
            assert name
            files[f"{base}/Opportunity/businessProcesses/{name.group(1)}.businessProcess-meta.xml"] = process
            members["BusinessProcess"].append(f"Opportunity.{name.group(1)}")
            files["force-app/main/default/standardValueSets/OpportunityStage.standardValueSet-meta.xml"] = \
                opportunity_stages_xml(pipeline)
            members["StandardValueSet"].append("OpportunityStage")
        else:
            add_field(object_api(model, pipeline.object_name), "Stage__c", stage_field_xml(pipeline))

    roles: list[AssociationLabel] = []
    for assoc in model.associations:
        ends = [next((o for o in model.objects if o.name == n), None) for n in (assoc.from_object, assoc.to_object)]
        from_custom, to_custom = (bool(e and e.custom) for e in ends)
        if (assoc.from_object, assoc.to_object) == ("contacts", "companies"):
            roles.append(assoc)
        elif from_custom and to_custom:
            a, b = object_api(model, assoc.from_object), object_api(model, assoc.to_object)
            junction = f"{api_name(assoc.name)}__c"
            label = assoc.label
            files[f"{base}/{junction}/{junction}.object-meta.xml"] = _xml(
                "CustomObject", _tag("deploymentStatus", "Deployed")
                + _tag("description", f"Junction for the {label!r} association between {a} and {b}.")
                + _tag("label", label) + "    <nameField>\n" + _tag("displayFormat", "LNK-{000000}", 2)
                + _tag("label", "Link", 2) + _tag("type", "AutoNumber", 2) + "    </nameField>\n"
                + _tag("pluralLabel", f"{label} links") + _tag("sharingModel", "ControlledByParent"))
            members["CustomObject"].append(junction)
            for order, (end, end_api) in enumerate(((assoc.from_object, a), (assoc.to_object, b))):
                end_name = api_name(end)
                add_field(junction, f"{end_name}__c", master_detail_xml(
                    f"{end_name}__c", model.object(end).singular, end_api, f"{api_name(assoc.name)}_{end_name}_Links",
                    f"{label} links", order))
        elif from_custom:
            source = object_api(model, assoc.from_object)
            target_name = api_name(assoc.to_object.rstrip("s").replace("companie", "company"))
            target = STANDARD.get(assoc.to_object, object_api(model, assoc.to_object))
            field = f"{api_name(assoc.name)}_{target_name}__c"
            add_field(source, field, lookup_xml(field, f"{assoc.label} ({target})", target,
                                                f"{api_name(model.object(assoc.from_object).name)}_{api_name(assoc.name)}",
                                                assoc.inverse_label or assoc.label))
    if roles:
        values = "".join("        <value>\n" + _tag("fullName", a.label, 3) + _tag("default", False, 3)
                         + _tag("label", a.label, 3) + "        </value>\n" for a in roles)
        files["force-app/main/default/standardValueSets/RoleOnAccountContact.notes.md"] = (
            "Contact-to-account association labels map to AccountContactRelation roles. Add these values to the "
            "Roles picklist (Setup > Account Contact Relationship Fields > Roles):\n\n"
            + "".join(f"- {a.label}\n" for a in roles)) + "\n<!--\n" + values + "-->\n"

    permission_set = permission_set_name(model)
    files[f"force-app/main/default/permissionsets/{permission_set}.permissionset-meta.xml"] = permission_set_xml(
        model, grantable, members["CustomObject"])
    members["PermissionSet"].append(permission_set)

    types = "".join("    <types>\n" + "".join(_tag("members", m, 2) for m in sorted(names)) + _tag("name", kind, 2)
                    + "    </types>\n" for kind, names in sorted(members.items()) if names)
    files["manifest/package.xml"] = _xml("Package", types + _tag("version", API_VERSION))
    files["sfdx-project.json"] = json.dumps({
        "packageDirectories": [{"path": "force-app", "default": True}]
        + ([{"path": "code", "default": False}] if model.key in HAND_WRITTEN else []),
        "name": f"crm-platform-{model.key}",
        "namespace": "", "sfdcLoginUrl": "https://login.salesforce.com", "sourceApiVersion": API_VERSION,
    }, indent=2) + "\n"
    return files


def problems(files: dict[str, str]) -> list[str]:
    """Salesforce rules a generated file breaks: API names, lengths, and XML that does not parse."""
    import xml.etree.ElementTree as ET

    found = []
    for path, text in files.items():
        if path.endswith(".json") or path.endswith(".md"):
            continue
        try:
            root = ET.fromstring(text.encode("utf-8"))
        except ET.ParseError as exc:
            found.append(f"{path}: {exc}")
            continue
        if path.endswith(".field-meta.xml"):
            name = root.findtext(f"{{{NS}}}fullName", "")
            bare = name.removesuffix("__c")
            if not API_NAME.match(bare) or len(bare) > 40:
                found.append(f"{path}: API name {name!r} breaks Salesforce's rules")
            if len(root.findtext(f"{{{NS}}}label", "")) > 40:
                found.append(f"{path}: label longer than 40 characters")
            relationship = root.findtext(f"{{{NS}}}relationshipName")
            if relationship is not None and (not API_NAME.match(relationship) or len(relationship) > 40):
                found.append(f"{path}: relationship name {relationship!r} breaks Salesforce's rules")
            for value in root.iter(f"{{{NS}}}value"):
                if len(value.findtext(f"{{{NS}}}fullName", "")) > 255:
                    found.append(f"{path}: picklist value longer than 255 characters")
        if path.endswith(".object-meta.xml"):
            bare = Path(path).name.split(".")[0].removesuffix("__c")
            if not API_NAME.match(bare) or len(bare) > 40:
                found.append(f"{path}: object API name breaks Salesforce's rules")
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile the tenant models to Salesforce metadata.")
    parser.add_argument("--check", action="store_true", help="fail if the committed metadata is stale")
    args = parser.parse_args()
    stale, written = [], 0
    for tenant in TENANTS.values():
        model = tenant.model()
        if not model.objects:
            continue
        files = build(model)
        if tenant.key in HAND_WRITTEN:
            files.update(parity_fixture(tenant.records()))
        issues = problems(files)
        if issues:
            sys.exit("\n".join(issues))
        for rel, text in files.items():
            path = OUT / tenant.key / rel
            if args.check:
                if not path.exists() or path.read_text(encoding="utf-8") != text:
                    stale.append(str(path.relative_to(ROOT)))
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            written += 1
        if not args.check:
            known = {OUT / tenant.key / rel for rel in files}
            for directory in GENERATED_DIRS:  # never code/, which people write
                for extra in (OUT / tenant.key / directory).rglob("*"):
                    if extra.is_file() and extra not in known:
                        extra.unlink()
    if stale:
        sys.exit("stale Salesforce metadata (run python -m crm_platform.salesforce.metadata): " + ", ".join(stale))
    print("Salesforce metadata is current" if args.check else f"wrote {written} Salesforce metadata files")


if __name__ == "__main__":
    main()
