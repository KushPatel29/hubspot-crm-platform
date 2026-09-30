"""Transaction-monitoring investigations as a CRM case-management build: two custom objects and a case pipeline.

Source: KushPatel29/aml-transaction-monitoring (``data/snapshots/aml``). Synthetic throughout; this is an
educational simulation of case management, not a compliance product, and nothing here files or decides anything.

* **Investigation case** (custom object, 366): priority, the rule that fired and the typology hypothesis, the
  evidence sentence, the next step and its deadline, in an "Investigation" pipeline. The stage, decision and
  filing status are set when a case is created and never again, because they belong to the investigator.
* **Subjects**: the 90 individuals as contacts and the 276 businesses as companies, associated with their case as
  "Subject".
* **Counterparty** (custom object, 286): the counterparties that at least two subjects of P0/P1 cases transact
  with (a shared counterparty is the network signal a single alert cannot show), associated with those cases as
  "Transacted with". The largest flows are summarised on the case.

``sla_started_at`` is stamped when a case is created in the portal, so its four-, 24- or 72-hour deadline runs from
when an investigator could first see it, not from when the synthetic data was generated.
"""

from __future__ import annotations

from collections import defaultdict

from crm_platform.model import (
    NOW,
    AssociationLabel,
    Link,
    ObjectModel,
    Pipeline,
    Property,
    Record,
    Stage,
    TenantModel,
    enum_of,
    key_property,
    options,
    pipeline_ref,
    slug,
    stage_ref,
)
from crm_platform.snapshots import money, number, rows

KEY = "aml"
NAME = "AML investigations"
GROUP = "aml_investigations"
PIPELINE = "Investigation"
STAGES = (Stage("Open triage"), Stage("Evidence requested"), Stage("Second-level review"),
          Stage("Closed: no further action", closed=True),
          Stage("Closed: referred for a reporting decision", closed=True))
SOURCE_STATES = {"OPEN TRIAGE": "Open triage", "EVIDENCE REQUESTED": "Evidence requested",
                 "SECOND-LEVEL REVIEW": "Second-level review"}
RULES = ("R1_STRUCTURING", "R2_LAYERING", "R3_SMURFING", "R4_ROUND_DOLLAR", "R5_DORMANT")
SUMMARY_LINKS = 5


def model() -> TenantModel:
    cases = rows(KEY, "cases.csv")
    subjects = rows(KEY, "subjects.csv")
    counterparties = rows(KEY, "counterparties.csv")

    def enum(name: str, label: str, values: list[str], description: str = "") -> Property:
        return Property(name, label, "enumeration", "select", description, enum_of(values))

    def num(name: str, label: str, description: str = "") -> Property:
        return Property(name, label, "number", "number", description)

    segments = sorted({s["segment"] for s in subjects})
    regions = sorted({s["home_region"] for s in subjects} | {c["home_region"] for c in counterparties})
    subject_props = (
        enum("aml_segment", "Customer segment", segments),
        enum("aml_home_region", "Home region", regions),
        Property("aml_opened_date", "Relationship opened", "date", "date"),
    )
    contacts = ObjectModel("contacts", GROUP, "AML investigations", (key_property("contacts"), *subject_props))
    companies = ObjectModel("companies", GROUP, "AML investigations", (key_property("companies"), *subject_props))
    case = ObjectModel(
        "investigation_case", "case_details", "Case details", (
            key_property("investigation_case", custom=True),
            Property("case_name", "Case", "string", "text"),
            Property("case_priority", "Priority", "enumeration", "select", "P0 is due in four hours.",
                     options(("p0", "P0"), ("p1", "P1"), ("p2", "P2"))),
            Property("subject_type", "Subject type", "enumeration", "select", "",
                     options(("individual", "Individual"), ("business", "Business"))),
            num("due_hours", "Hours to first action"),
            Property("sla_started_at", "SLA clock started", "datetime", "date",
                     "When the case reached the portal; the deadline is this plus the hours to first action."),
            num("max_risk_score", "Highest risk score"),
            Property("rules_fired", "Rules fired", "enumeration", "checkbox", "", enum_of(list(RULES))),
            enum("typology_hypothesis", "Typology hypothesis", sorted({c["typology_hypothesis"] for c in cases})),
            num("flagged_transactions", "Flagged transactions"),
            num("amount_involved_cad", "Amount involved (CAD)"),
            Property("evidence_references", "Evidence", "string", "textarea"),
            Property("recommended_next_step", "Next step", "string", "textarea"),
            Property("counterparty_summary", "Largest shared counterparties", "string", "textarea"),
            enum("decision_status", "Decision", ["HUMAN DECISION REQUIRED", "NO FURTHER ACTION",
                                                 "REFERRED FOR REPORTING DECISION"]),
            enum("filing_status", "Reporting status", ["NOT ASSESSED OR FILED"],
                 "The simulation never files; the only value says so."),
        ),
        custom=True, singular="Investigation case", plural="Investigation cases", primary_display="case_name",
        secondary_display=("case_priority", "typology_hypothesis"), searchable=("case_name",),
        required=("case_name",), associated_objects=("contacts", "companies"), has_pipeline=True,
    )
    counterparty = ObjectModel(
        "counterparty", "counterparty_details", "Counterparty details", (
            key_property("counterparty", custom=True),
            Property("counterparty_name", "Counterparty", "string", "text"),
            enum("counterparty_type", "Type", sorted({c["counterparty_type"] for c in counterparties})),
            enum("counterparty_region", "Home region", regions),
            num("hot_subjects", "P0/P1 subjects transacting",
                "How many subjects of P0 or P1 cases send money to or receive it from this counterparty."),
        ),
        custom=True, singular="Counterparty", plural="Counterparties", primary_display="counterparty_name",
        secondary_display=("counterparty_type", "hot_subjects"), searchable=("counterparty_name",),
        required=("counterparty_name",),
    )
    return TenantModel(
        KEY, NAME, "Synthetic transaction-monitoring case management: cases, subjects and shared counterparties.",
        objects=(contacts, companies, case, counterparty),
        pipelines=(Pipeline("investigation_case", PIPELINE, STAGES),),
        associations=(
            AssociationLabel("investigation_case", "contacts", "subject", "Subject", "Subject of case"),
            AssociationLabel("investigation_case", "companies", "subject", "Subject", "Subject of case"),
            AssociationLabel("investigation_case", "counterparty", "transacted_with", "Transacted with",
                             "Transacted with case subject"),
        ),
    )


def records() -> list[Record]:
    cases = rows(KEY, "cases.csv")
    subjects = {s["entity_id"]: s for s in rows(KEY, "subjects.csv")}
    parties = {c["counterparty_id"]: c for c in rows(KEY, "counterparties.csv")}
    links: dict[str, list[dict[str, str]]] = defaultdict(list)
    for link in rows(KEY, "links.csv"):
        links[link["entity_id"]].append(link)
    out: list[Record] = []

    for s in subjects.values():
        shared = {"aml_segment": slug(s["segment"]), "aml_home_region": slug(s["home_region"]),
                  "aml_opened_date": s["opened_date"]}
        if s["entity_type"] == "individual":
            first, _, last = s["display_name"].partition(" ")
            out.append(Record("contacts", s["entity_id"], {"firstname": first, "lastname": last, **shared}))
        else:
            out.append(Record("companies", s["entity_id"], {"name": s["display_name"], **shared}))

    for party in parties.values():
        out.append(Record("counterparty", party["counterparty_id"], {
            "counterparty_name": f"{party['display_name']} ({party['counterparty_id']})",
            "counterparty_type": slug(party["counterparty_type"]), "counterparty_region": slug(party["home_region"]),
            "hot_subjects": party["hot_subjects"],
        }))

    for c in cases:
        subject = subjects[c["entity_id"]]
        flows = sorted(links.get(c["entity_id"], []), key=lambda e: (-float(e["amount_cad"]), e["counterparty_id"]))
        summary = "\n".join(
            f"{parties[e['counterparty_id']]['display_name']} ({e['counterparty_id']}, "
            f"{parties[e['counterparty_id']]['counterparty_type']}): ${float(e['amount_cad']):,.0f} over "
            f"{e['transactions']} transactions, {e['directions'].replace(';', ' and ')}; "
            f"{parties[e['counterparty_id']]['hot_subjects']} flagged subjects share it"
            for e in flows[:SUMMARY_LINKS])
        rules = [slug(r) for r in c["rules_fired"].split(";") if r and r != "NONE"]
        props = {
            "case_name": f"{c['investigation_id']} · {c['display_name']}",
            "case_priority": slug(c["priority"]), "subject_type": c["entity_type"],
            "due_hours": c["due_hours"], "sla_started_at": NOW, "max_risk_score": number(c["max_risk_score"]),
            "rules_fired": ";".join(rules), "typology_hypothesis": slug(c["typology_hypothesis"]),
            "flagged_transactions": c["flagged_transactions"], "amount_involved_cad": money(c["amount_involved_cad"]),
            "evidence_references": c["evidence_references"], "recommended_next_step": c["recommended_next_step"],
            "counterparty_summary": summary or "No counterparty is shared with another P0/P1 subject.",
            "hs_pipeline": pipeline_ref("investigation_case", PIPELINE),
            "hs_pipeline_stage": stage_ref("investigation_case", PIPELINE, SOURCE_STATES[c["case_state"]]),
            "decision_status": slug(c["decision_status"]), "filing_status": slug(c["filing_status"]),
        }
        subject_object = "contacts" if subject["entity_type"] == "individual" else "companies"
        case_links = [Link(subject_object, c["entity_id"], "subject")]
        case_links += [Link("counterparty", e["counterparty_id"], "transacted_with") for e in flows]
        out.append(Record("investigation_case", c["investigation_id"], props, links=tuple(case_links),
                          create_only=frozenset({"sla_started_at", "hs_pipeline", "hs_pipeline_stage",
                                                 "decision_status", "filing_status"})))
    return out
