"""CRM-as-code: plans are complete and ordered, apply converges, and nothing a person made is deleted or retyped."""

from __future__ import annotations

import pytest

from crm_platform.hubspot import schema
from crm_platform.model import TenantModel
from crm_platform.tenants import TENANTS

LOADED = [k for k in sorted(TENANTS) if TENANTS[k].model().objects]


@pytest.mark.parametrize("key", LOADED)
def test_apply_converges_and_a_second_apply_writes_nothing(key, client):
    model = TENANTS[key].model()
    result = schema.apply(client, model)
    assert result["converged"] and not result["conflicts"] and not result["drift"]
    writes = client.writes
    again = schema.apply(client, model)
    assert again["applied"] == [] and client.writes == writes


def test_the_first_plan_creates_custom_objects_before_their_properties_and_pipelines(client):
    model = TENANTS["aml"].model()
    ops = schema.plan(model, schema.read_state(client, model)).ops
    kinds = [op.kind for op in ops]
    assert kinds[:2] == ["create_schema", "create_schema"]
    assert kinds.index("create_schema_association") > kinds.index("create_schema")
    assert "create_pipeline" not in kinds  # planned on the pass after the custom object exists
    result = schema.apply(client, model)
    assert any(line.startswith("create_pipeline investigation_case") for line in result["applied"])


def test_a_portal_property_the_model_does_not_declare_is_drift_and_is_kept(client, fake):
    model = TENANTS["crosssell"].model()
    schema.apply(client, model)
    fake.properties["0-2"]["xsell_legacy_flag"] = {"name": "xsell_legacy_flag", "label": "Legacy", "type": "string",
                                                   "fieldType": "text", "groupName": "cross_sell", "options": []}
    result = schema.apply(client, model)
    assert result["drift"] == ["companies.xsell_legacy_flag is in group cross_sell but not in the model"]
    assert "xsell_legacy_flag" in fake.properties["0-2"]
    assert not any(method == "DELETE" for method, _ in fake.calls)


def test_a_retyped_property_is_a_conflict_not_a_change(client, fake):
    model = TENANTS["meridian"].model()
    schema.apply(client, model)
    fake.properties["0-2"]["meridian_price_sensitivity"]["type"] = "string"
    fake.properties["0-2"]["meridian_price_sensitivity"]["fieldType"] = "text"
    plan = schema.plan(model, schema.read_state(client, model))
    assert plan.converged
    assert [c.target for c in plan.conflicts] == ["meridian_price_sensitivity"]


def test_options_are_merged_never_removed(client, fake):
    model = TENANTS["meridian"].model()
    schema.apply(client, model)
    tier = fake.properties["0-2"]["meridian_tier"]
    tier["options"] = [o for o in tier["options"] if o["value"] != "a"]
    tier["options"].append({"label": "Strategic", "value": "strategic", "displayOrder": 9, "hidden": False})
    plan = schema.plan(model, schema.read_state(client, model))
    [op] = plan.ops
    assert (op.kind, op.target) == ("update_property", "meridian_tier")
    values = [o["value"] for o in op.payload["options"]]
    assert "strategic" in values and "a" in values
    schema.apply(client, model)
    assert {"a", "strategic"} <= {o["value"] for o in fake.properties["0-2"]["meridian_tier"]["options"]}


def test_a_missing_stage_is_added_to_the_existing_pipeline(client, fake):
    model = TENANTS["meridian"].model()
    schema.apply(client, model)
    pipeline = next(p for p in fake.pipelines["0-3"] if p["label"] == "Quote to order")
    pipeline["stages"] = [s for s in pipeline["stages"] if s["label"] != "Pricing review"]
    [op] = schema.plan(model, schema.read_state(client, model)).ops
    assert (op.kind, op.target) == ("create_stage", "Quote to order/Pricing review")
    assert schema.apply(client, model)["converged"]


def test_an_app_that_needs_properties_another_system_owns_is_refused_before_anything_is_written(client):
    model = TENANTS["scalelab"].model()
    with pytest.raises(RuntimeError, match="growthops_net_cash"):
        schema.apply(client, model)
    assert client.writes == 0


def test_requirements_that_exist_pass_without_writing(client, fake):
    model = TenantModel("x", "X", "", requires=(("contacts", "lifecyclestage"),))
    assert schema.apply(client, model)["converged"] and client.writes == 0
