"""The model rules that make one declaration compile to both HubSpot and Salesforce."""

from __future__ import annotations

import dataclasses

import pytest

from crm_platform.model import (
    KEY_PROPERTY,
    AssociationLabel,
    ObjectModel,
    Pipeline,
    Property,
    Stage,
    TenantModel,
    key_property,
    options,
    slug,
)
from crm_platform.tenants import TENANTS


@pytest.mark.parametrize("key", sorted(TENANTS))
def test_every_tenant_model_compiles_to_both_crms(key):
    assert TENANTS[key].model().problems() == []


def _tenant(*objects, pipelines=(), associations=()):
    return TenantModel("t", "T", "", objects=tuple(objects), pipelines=tuple(pipelines),
                       associations=tuple(associations))


def _companies(*props):
    return ObjectModel("companies", "grp", "Group", (key_property("companies"), *props))


def test_names_must_fit_hubspot_and_salesforce():
    long_name = "x" * 41
    found = _tenant(_companies(Property("hs_mine", "Mine", "string", "text"),
                               Property(long_name, "Long", "string", "text"),
                               Property("Bad-Name", "Bad", "string", "text"),
                               Property("double__under", "Double", "string", "text"))).problems()
    assert any("hs_mine" in p for p in found)
    assert any(long_name in p for p in found)
    assert any("Bad-Name" in p for p in found)
    assert any("double__under" in p for p in found)


def test_properties_must_be_typed_consistently():
    found = _tenant(_companies(
        Property("level", "Level", "enumeration", "select"),  # no options
        Property("score", "Score", "number", "text"),  # field type does not fit
        Property("tagged", "Tagged", "string", "text", options=options(("a", "A"))),  # options on a string
        Property("dupes", "Dupes", "enumeration", "select", options=options(("a", "A"), ("a", "B"))),
    )).problems()
    assert {p.split(": ")[0].split(".")[-1] for p in found} >= {"level", "score", "tagged", "dupes"}


def test_every_object_carries_the_key_and_unique_only_where_hubspot_allows():
    no_key = ObjectModel("companies", "grp", "Group", ())
    assert any(KEY_PROPERTY in p for p in _tenant(no_key).problems())
    products = ObjectModel("products", "grp", "Group", (
        dataclasses.replace(key_property("products"), unique=True),))
    assert any("unique" in p for p in _tenant(products).problems())
    assert _tenant(ObjectModel("products", "grp", "Group", (key_property("products"),))).problems() == []


def test_custom_objects_need_labels_and_a_declared_display_property():
    thing = ObjectModel("thing", "thing_details", "Thing", (key_property("thing", custom=True),), custom=True,
                        primary_display="title", searchable=("nope",))
    found = _tenant(thing).problems()
    assert any("singular and plural" in p for p in found)
    assert any("primary display" in p for p in found)
    assert any("searchable" in p for p in found)
    assert any("not a standard object" in p for p in _tenant(dataclasses.replace(thing, custom=False)).problems())


def test_pipelines_need_open_and_closed_stages_and_deal_probabilities():
    found = _tenant(pipelines=[
        Pipeline("deals", "No probability", (Stage("Open"), Stage("Won", closed=True, probability=1.0))),
        Pipeline("deals", "All open", (Stage("A", probability=0.1), Stage("B", probability=0.2))),
        Pipeline("deals", "Half won", (Stage("A", probability=0.1), Stage("Won", closed=True, probability=0.5))),
        Pipeline("companies", "Nope", (Stage("A"), Stage("B", closed=True))),
    ]).problems()
    assert any("No probability" in p and "probability" in p for p in found)
    assert any("All open" in p for p in found)
    assert any("Half won" in p for p in found)
    assert any("cannot have a pipeline" in p for p in found)


def test_association_labels_must_join_known_objects():
    found = _tenant(associations=[AssociationLabel("contacts", "spaceships", "pilot", "Pilot")]).problems()
    assert any("unknown object spaceships" in p for p in found)


def test_slug_turns_source_labels_into_stable_option_values():
    assert slug("Second-level review") == "second_level_review"
    assert slug("2/10 Net 30") == "2_10_net_30"
    assert slug("At Risk (was valuable)") == "at_risk_was_valuable"
