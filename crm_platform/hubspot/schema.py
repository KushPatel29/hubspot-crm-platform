"""CRM-as-code for HubSpot: read a portal's schema, plan the difference from a :class:`TenantModel`, apply it.

``plan`` is pure: it compares the model with a :class:`PortalState` read from the portal and returns the operations
that would make the portal match, in dependency order (custom objects before their properties, pipelines before
anything is loaded into them, association labels last). ``apply`` runs them, re-reads the portal, and plans again
until nothing is left, which is what makes the result a verified convergence rather than a list of calls that
returned 200.

What it will never do on its own:

* **Delete or rename anything.** A property, option, stage or label in the portal that the model does not declare is
  reported as drift and left alone; people may be using it.
* **Change a property's type or uniqueness.** HubSpot cannot convert either in place without risking the data in
  it. The operation is planned as a *conflict*, which blocks nothing else and is reported for a person to decide.
* **Remove an enumeration option.** Options are merged: new ones are appended and labels updated, existing ones kept.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from crm_platform.hubspot.client import HubSpotClient
from crm_platform.model import ObjectModel, Pipeline, Property, TenantModel

STANDARD_TYPE_IDS = {"contacts": "0-1", "companies": "0-2", "deals": "0-3", "tickets": "0-5", "products": "0-7",
                     "line_items": "0-8"}
SCHEMA_OBJECT_NAMES = {"contacts": "CONTACT", "companies": "COMPANY", "deals": "DEAL", "tickets": "TICKET",
                       "products": "PRODUCT", "line_items": "LINE_ITEM"}
ORDER = ("create_schema", "create_schema_association", "create_group", "create_properties", "update_property",
         "create_pipeline", "create_stage", "update_stage", "create_label")


@dataclass(frozen=True)
class Op:
    kind: str
    object_name: str
    target: str
    payload: Any = None
    note: str = ""

    def describe(self) -> str:
        return f"{self.kind} {self.object_name} {self.target}" + (f" ({self.note})" if self.note else "")


@dataclass
class PortalState:
    type_ids: dict[str, str] = field(default_factory=dict)
    properties: dict[str, dict[str, dict]] = field(default_factory=dict)
    groups: dict[str, set[str]] = field(default_factory=dict)
    pipelines: dict[str, list[dict]] = field(default_factory=dict)
    labels: dict[tuple[str, str], list[dict]] = field(default_factory=dict)
    schema_associations: set[tuple[str, str]] = field(default_factory=set)


@dataclass
class Plan:
    ops: list[Op]
    conflicts: list[Op]
    drift: list[str]
    missing_requirements: list[str]

    @property
    def converged(self) -> bool:
        return not self.ops

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for op in self.ops:
            counts[op.kind] = counts.get(op.kind, 0) + (len(op.payload) if op.kind == "create_properties" else 1)
        return counts


# ---------------------------------------------------------------- compile: model -> HubSpot payloads

def property_payload(prop: Property, group: str) -> dict:
    body: dict[str, Any] = {"name": prop.name, "label": prop.label, "type": prop.type, "fieldType": prop.field_type,
                            "groupName": group, "description": prop.description}
    if prop.type == "enumeration":
        body["options"] = [{"label": o.label, "value": o.value, "displayOrder": i, "hidden": False}
                           for i, o in enumerate(prop.options)]
    if prop.type == "bool":
        body["options"] = [{"label": "Yes", "value": "true", "displayOrder": 0, "hidden": False},
                           {"label": "No", "value": "false", "displayOrder": 1, "hidden": False}]
    if prop.unique:
        body["hasUniqueValue"] = True
    return body


def schema_payload(obj: ObjectModel) -> dict:
    """A custom object with only the properties its schema must name; the rest are created like any others."""
    needed = {obj.primary_display, *obj.secondary_display, *obj.searchable, *obj.required}
    return {
        "name": obj.name,
        "labels": {"singular": obj.singular, "plural": obj.plural},
        "primaryDisplayProperty": obj.primary_display,
        "secondaryDisplayProperties": list(obj.secondary_display),
        "searchableProperties": list(obj.searchable),
        "requiredProperties": list(obj.required),
        "properties": [{k: v for k, v in property_payload(p, obj.group).items() if k != "groupName"}
                       for p in obj.properties if p.name in needed],
        "associatedObjects": [SCHEMA_OBJECT_NAMES[o] for o in obj.associated_objects],
    }


def stage_payload(object_name: str, stage_index: int, pipeline: Pipeline) -> dict:
    stage = pipeline.stages[stage_index]
    metadata: dict[str, str] = {}
    if object_name == "deals":
        metadata = {"probability": str(stage.probability), "isClosed": str(stage.closed).lower()}
    elif object_name == "tickets":
        metadata = {"ticketState": "CLOSED" if stage.closed else "OPEN"}
    else:
        metadata = {"isClosed": str(stage.closed).lower()}
    return {"label": stage.label, "displayOrder": stage_index, "metadata": metadata}


def pipeline_payload(pipeline: Pipeline) -> dict:
    return {"label": pipeline.label, "displayOrder": 0,
            "stages": [stage_payload(pipeline.object_name, i, pipeline) for i in range(len(pipeline.stages))]}


# ---------------------------------------------------------------- read: portal -> PortalState

def type_id(state: PortalState, object_name: str) -> str | None:
    return STANDARD_TYPE_IDS.get(object_name) or state.type_ids.get(object_name)


def read_state(client: HubSpotClient, model: TenantModel) -> PortalState:
    state = PortalState()
    custom = {o.name for o in model.objects if o.custom}
    schemas = client.get("/crm/v3/schemas").get("results", []) if custom else []
    by_id: dict[str, str] = {}
    for schema in schemas:
        if schema.get("name") in custom:
            state.type_ids[schema["name"]] = schema["objectTypeId"]
        by_id[schema["objectTypeId"]] = schema.get("name", "")
    for schema in schemas:
        for assoc in schema.get("associations") or []:
            state.schema_associations.add((assoc.get("fromObjectTypeId", ""), assoc.get("toObjectTypeId", "")))
    names = {o.name for o in model.objects} | {obj for obj, _ in model.requires}
    for name in sorted(names):
        object_type = type_id(state, name)
        if object_type is None:
            continue
        state.properties[name] = {p["name"]: p for p in client.get(f"/crm/v3/properties/{object_type}")["results"]}
        state.groups[name] = {g["name"] for g in client.get(f"/crm/v3/properties/{object_type}/groups")["results"]}
    for name in sorted({p.object_name for p in model.pipelines}):
        object_type = type_id(state, name)
        if object_type is not None:
            state.pipelines[name] = client.get(f"/crm/v3/pipelines/{object_type}")["results"]
    for assoc in model.associations:
        pair = (assoc.from_object, assoc.to_object)
        ids = (type_id(state, pair[0]), type_id(state, pair[1]))
        if pair not in state.labels and ids[0] and ids[1]:
            state.labels[pair] = client.get(f"/crm/v4/associations/{ids[0]}/{ids[1]}/labels")["results"]
    return state


# ---------------------------------------------------------------- plan: model x state -> ops

def _options(body: dict) -> list[tuple[str, str]]:
    return [(o["value"], o["label"]) for o in body.get("options") or []]


def _merged_options(desired: dict, actual: dict) -> list[dict]:
    wanted = dict(_options(desired))
    merged = [{"label": wanted.get(value, label), "value": value, "displayOrder": i, "hidden": False}
              for i, (value, label) in enumerate(_options(actual))]
    have = {o["value"] for o in merged}
    merged += [{"label": label, "value": value, "displayOrder": len(merged) + i, "hidden": False}
               for i, (value, label) in enumerate((v, lab) for v, lab in _options(desired) if v not in have)]
    return merged


def plan(model: TenantModel, state: PortalState) -> Plan:
    ops: list[Op] = []
    conflicts: list[Op] = []
    drift: list[str] = []
    missing: list[str] = []

    for obj in model.objects:
        if obj.custom and obj.name not in state.type_ids:
            ops.append(Op("create_schema", obj.name, obj.singular, schema_payload(obj)))
    for assoc in model.associations:
        ends = [next((o for o in model.objects if o.name == end), None) for end in (assoc.from_object, assoc.to_object)]
        if all(o is not None and o.custom for o in ends):
            ids = (type_id(state, assoc.from_object), type_id(state, assoc.to_object))
            if not (ids[0] and ids[1]):
                ops.append(Op("create_schema_association", assoc.from_object, assoc.to_object, None,
                              "after both custom objects exist"))
            elif ids not in state.schema_associations and ids[::-1] not in state.schema_associations:
                ops.append(Op("create_schema_association", assoc.from_object, assoc.to_object,
                              {"fromObjectTypeId": ids[0], "toObjectTypeId": ids[1], "name": assoc.name}))

    for obj in model.objects:
        actual = state.properties.get(obj.name)
        if actual is None:  # a custom object not created yet: everything follows its schema
            continue
        if obj.group not in state.groups.get(obj.name, set()):
            ops.append(Op("create_group", obj.name, obj.group, {"name": obj.group, "label": obj.group_label,
                                                                 "displayOrder": -1}))
        creates = []
        for prop in obj.properties:
            desired = property_payload(prop, obj.group)
            current = actual.get(prop.name)
            if current is None:
                creates.append(desired)
                continue
            if (current.get("type"), current.get("fieldType")) != (prop.type, prop.field_type):
                conflicts.append(Op("update_property", obj.name, prop.name, None,
                                    f"portal has {current.get('type')}/{current.get('fieldType')}, model "
                                    f"{prop.type}/{prop.field_type}; not changed automatically"))
                continue
            if bool(current.get("hasUniqueValue")) != prop.unique:
                conflicts.append(Op("update_property", obj.name, prop.name, None,
                                    "uniqueness differs; HubSpot cannot change it in place"))
                continue
            change: dict[str, Any] = {}
            for key in ("label", "description", "groupName"):
                if (current.get(key) or "") != (desired.get(key) or ""):
                    change[key] = desired[key]
            if prop.type == "enumeration":
                merged = _merged_options(desired, current)
                if [(o["value"], o["label"]) for o in merged] != _options(current):
                    change["options"] = merged
            if change:
                ops.append(Op("update_property", obj.name, prop.name, change, ", ".join(sorted(change))))
        if creates:
            ops.append(Op("create_properties", obj.name, f"{len(creates)} properties", creates))
        declared = set(obj.names)
        drift += [f"{obj.name}.{name} is in group {obj.group} but not in the model"
                  for name, body in sorted(actual.items())
                  if body.get("groupName") == obj.group and name not in declared]

    for pipeline in model.pipelines:
        existing = state.pipelines.get(pipeline.object_name)
        if existing is None:
            if type_id(state, pipeline.object_name) is None:
                continue  # its custom object is created first; planned on the next pass
            existing = []
        found = next((p for p in existing if p.get("label") == pipeline.label), None)
        if found is None:
            ops.append(Op("create_pipeline", pipeline.object_name, pipeline.label, pipeline_payload(pipeline)))
            continue
        stages = {s["label"]: s for s in found.get("stages", [])}
        for i, stage in enumerate(pipeline.stages):
            body = stage_payload(pipeline.object_name, i, pipeline)
            current = stages.get(stage.label)
            if current is None:
                ops.append(Op("create_stage", pipeline.object_name, f"{pipeline.label}/{stage.label}",
                              {"pipeline_id": found["id"], **body}))
            elif any(str((current.get("metadata") or {}).get(k)) != v for k, v in body["metadata"].items()):
                ops.append(Op("update_stage", pipeline.object_name, f"{pipeline.label}/{stage.label}",
                              {"pipeline_id": found["id"], "stage_id": current["id"], **body}, "metadata"))
        declared = {s.label for s in pipeline.stages}
        drift += [f"{pipeline.object_name} pipeline {pipeline.label} has stage {label} not in the model"
                  for label in sorted(set(stages) - declared)]

    for assoc in model.associations:
        pair = (assoc.from_object, assoc.to_object)
        labels = state.labels.get(pair)
        if labels is None:
            continue  # an end does not exist yet
        if not any(item.get("label") == assoc.label for item in labels):
            body = {"label": assoc.label, "name": assoc.name}
            if assoc.inverse_label:
                body["inverseLabel"] = assoc.inverse_label
            ops.append(Op("create_label", assoc.from_object, f"{assoc.to_object}:{assoc.label}", body))

    for object_name, prop_name in model.requires:
        if prop_name not in state.properties.get(object_name, {}):
            missing.append(f"{object_name}.{prop_name}")

    ops.sort(key=lambda op: ORDER.index(op.kind))
    return Plan(ops, conflicts, drift, missing)


# ---------------------------------------------------------------- apply

def _run(client: HubSpotClient, state: PortalState, op: Op) -> None:
    object_type = type_id(state, op.object_name)
    if op.kind == "create_schema":
        client.post("/crm/v3/schemas", op.payload)
    elif op.kind == "create_schema_association":
        if op.payload is None:
            return
        client.post(f"/crm/v3/schemas/{op.payload['fromObjectTypeId']}/associations", op.payload)
    elif op.kind == "create_group":
        client.post(f"/crm/v3/properties/{object_type}/groups", op.payload)
    elif op.kind == "create_properties":
        for start in range(0, len(op.payload), 100):
            client.post(f"/crm/v3/properties/{object_type}/batch/create", {"inputs": op.payload[start:start + 100]})
    elif op.kind == "update_property":
        client.patch(f"/crm/v3/properties/{object_type}/{op.target}", op.payload)
    elif op.kind == "create_pipeline":
        client.post(f"/crm/v3/pipelines/{object_type}", op.payload)
    elif op.kind == "create_stage":
        body = {k: v for k, v in op.payload.items() if k != "pipeline_id"}
        client.post(f"/crm/v3/pipelines/{object_type}/{op.payload['pipeline_id']}/stages", body)
    elif op.kind == "update_stage":
        body = {k: v for k, v in op.payload.items() if k not in ("pipeline_id", "stage_id")}
        client.patch(f"/crm/v3/pipelines/{object_type}/{op.payload['pipeline_id']}/stages/{op.payload['stage_id']}",
                     body)
    elif op.kind == "create_label":
        to_type = type_id(state, op.target.split(":")[0])
        client.post(f"/crm/v4/associations/{object_type}/{to_type}/labels", op.payload)
    else:  # pragma: no cover - ORDER lists every kind
        raise ValueError(op.kind)


def apply(client: HubSpotClient, model: TenantModel, *, max_passes: int = 4) -> dict:
    """Apply, re-read and re-plan until the portal matches the model. Returns what was done and the final plan."""
    done: list[str] = []
    for _ in range(max_passes):
        state = read_state(client, model)
        current = plan(model, state)
        if current.missing_requirements:
            raise RuntimeError("the portal lacks properties this tenant's app needs (owned elsewhere): "
                               + ", ".join(current.missing_requirements))
        if current.converged:
            return {"applied": done, "converged": True, "conflicts": [c.describe() for c in current.conflicts],
                    "drift": current.drift, "summary": {}}
        runnable = [op for op in current.ops if op.payload is not None]
        for op in runnable:
            _run(client, state, op)
            done.append(op.describe())
    final = plan(model, read_state(client, model))
    return {"applied": done, "converged": final.converged, "conflicts": [c.describe() for c in final.conflicts],
            "drift": final.drift, "summary": final.summary()}


def describe(ops: Iterable[Op]) -> list[str]:
    return [op.describe() for op in ops]
