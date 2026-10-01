"""Loading records: keyed, idempotent, respectful of what people changed, and associated with the right labels."""

from __future__ import annotations

import pytest

from crm_platform.hubspot import records, schema
from crm_platform.hubspot.records import same
from crm_platform.tenants import TENANTS

LOADED = [k for k in sorted(TENANTS) if TENANTS[k].model().objects]


def _load(client, key):
    tenant = TENANTS[key]
    model = tenant.model()
    schema.apply(client, model)
    return model, tenant.records(), records.load(client, model, tenant.records())


@pytest.mark.parametrize("key", LOADED)
def test_a_load_then_a_rerun_writes_nothing(key, client):
    model, recs, first = _load(client, key)
    assert not first["failures"]
    assert all(o["create"] == o["desired"] for o in first["objects"].values())
    writes = client.writes
    second = records.load(client, model, recs)
    assert records.converged(second) and client.writes == writes


def test_a_dry_run_plans_but_writes_nothing(client):
    model = TENANTS["crosssell"].model()
    schema.apply(client, model)
    writes = client.writes
    report = records.load(client, model, TENANTS["crosssell"].records(), write=False)
    assert report["objects"]["recommendation"]["create"] == 302 and client.writes == writes


def test_labels_land_on_the_right_type(client, fake):
    _load(client, "crosssell")
    company = fake.by_key("companies", "CUST-001")
    contact = fake.by_key("contacts", "CUST-001-buyer")
    kinds = {t for (fa, fid, fb, tid, t) in fake.links if (fa, fid, fb, tid) == ("0-1", contact["id"], "0-2",
                                                                                   company["id"])}
    labels = {t["label"] for t in fake.types[("0-1", "0-2")] if t["typeId"] in kinds}
    assert labels == {"Buyer", "Primary"}


def test_a_value_a_rep_changed_on_a_create_only_field_is_not_put_back(client, fake):
    model, recs, _ = _load(client, "crosssell")
    offer = fake.by_key("recommendation", "CUST-001:SKU-012")
    offer["properties"]["offer_status"] = "accepted"
    report = records.load(client, model, recs)
    assert report["objects"]["recommendation"]["update"] == 0
    assert fake.by_key("recommendation", "CUST-001:SKU-012")["properties"]["offer_status"] == "accepted"


def test_a_changed_source_value_is_updated_and_only_that_field(client, fake):
    model, recs, _ = _load(client, "meridian")
    company = fake.by_key("companies", "CU2000")
    company["properties"]["meridian_price_sensitivity"] = "1"
    report = records.load(client, model, recs)
    assert report["objects"]["companies"]["update"] == 1
    assert report["objects"]["companies"]["fields_changed"] == ["meridian_price_sensitivity"]


def test_records_without_a_key_are_left_alone_and_orphans_are_reported_not_deleted(client, fake):
    model, recs, _ = _load(client, "aml")
    fake._write("0-1", None, {"firstname": "Sample", "lastname": "Contact"})
    kept = recs[1:]
    removed = recs[0]
    report = records.load(client, model, kept)
    assert report["objects"]["contacts"]["unmanaged"] == 1
    assert report["objects"][removed.object_name]["orphans"] == 1
    assert fake.by_key(removed.object_name, removed.key)


def test_line_items_point_at_their_product_and_stages_resolve_by_label(client, fake):
    _load(client, "meridian")
    line = fake.by_key("line_items", "Q105113") if any(
        r["properties"].get("crm_platform_key") == "Q105113" for r in fake.records("line_items")) else \
        fake.records("line_items")[0]
    product = next(r for r in fake.records("products") if r["id"] == line["properties"]["hs_product_id"])
    assert product["properties"]["name"] == line["properties"]["name"]
    won = next(p for p in fake.pipelines["0-3"] if p["label"] == "Quote to order")
    stage_labels = {s["id"]: s["label"] for s in won["stages"]}
    stages = {stage_labels[r["properties"]["dealstage"]] for r in fake.records("deals")}
    assert stages == {"Won", "Lost"}


def test_a_partial_batch_failure_is_reported_with_its_category(client, fake):
    model = TENANTS["crosssell"].model()
    schema.apply(client, model)
    fake.properties["0-2"]["xsell_churn_risk"]["options"] = []  # every company write now fails validation
    report = records.load(client, model, TENANTS["crosssell"].records())
    failures = [f for f in report["failures"] if f["object"] == "companies"]
    assert failures and {f["category"] for f in failures} == {"VALIDATION_ERROR"}
    assert not records.converged(report)


@pytest.mark.parametrize(("name", "desired", "actual", "equal"), [
    ("price", "12.50", "12.5", True),
    ("amount", "100.00", "100.004", True),
    ("price", "12.50", "12.51", False),
    ("closedate", "2026-04-30", "2026-04-30T00:00:00Z", True),
    ("email", "A.B@x.example.com", "a.b@x.example.com", True),
    ("rules_fired", "r1;r2", "r2;r1", True),
    ("name", "Acme", "", False),
])
def test_values_compare_the_way_hubspot_stores_them(name, desired, actual, equal):
    assert same(name, desired, actual) is equal


def test_two_records_with_one_key_are_reported_and_block_convergence(client, fake):
    model, recs, _ = _load(client, "meridian")
    original = fake.by_key("products", recs[next(i for i, r in enumerate(recs) if r.object_name == "products")].key)
    imported = {k: v for k, v in original["properties"].items() if k != "hs_object_id"}
    copy, _ = fake._write("0-7", None, imported)  # a duplicate someone imported by hand
    # HubSpot does not issue IDs in creation order: give the newer copy the smaller ID
    store = fake.objects["0-7"]
    store.pop(copy["id"])
    copy["id"] = copy["properties"]["hs_object_id"] = "1"
    store["1"] = copy
    report = records.load(client, model, recs)
    assert report["objects"]["products"]["duplicates"] == 1
    assert report["objects"]["products"]["create"] == 0 and report["objects"]["products"]["update"] == 0
    assert not records.converged(report)
    assert fake.by_key("products", original["properties"]["crm_platform_key"])["id"] == original["id"]
    assert any(r["id"] == copy["id"] for r in fake.records("products"))  # reported, never deleted
    state = schema.read_state(client, model)
    existing = records.read_records(client, state, "products", {"name"})
    plan = records.plan_object("products", [], existing, lambda v: v)
    assert plan.duplicates == {original["properties"]["crm_platform_key"]: ["1"]}  # the older record is kept


def test_a_reference_that_does_not_resolve_says_which_and_why(client, fake):
    model = TENANTS["meridian"].model()
    state = schema.read_state(client, model)
    resolve = records.Resolver(state, {"products": {}}, "now")
    with pytest.raises(ValueError, match="no products record with that key"):
        resolve("@record:products:NOPE")
    with pytest.raises(ValueError, match="no deals pipeline labelled 'Nowhere'"):
        resolve("@stage:deals:Nowhere:Won")
