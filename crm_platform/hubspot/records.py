"""Load a tenant's records into its portal by the source system's key, and prove the load converged.

For each object the loader reads every record the portal holds, matches them to the desired records by
``crm_platform_key``, and plans three things: records to create, fields to update on records that exist, and
associations to add. Nothing is ever deleted or unlinked; a keyed record the source no longer has is reported as an
orphan, and a record without a key (HubSpot's own sample contacts) is left alone and counted. Two records with the
same key (possible on products and line items, where HubSpot cannot make the key unique) are reported as duplicates:
the oldest is the one the loader keeps in step, and the portal does not count as converged until a person merges or
removes the others.

Values are compared the way HubSpot stores them, so a rerun plans nothing: numbers as numbers (``"12.50"`` is
``"12.5"``), a date property against HubSpot's midnight-UTC datetime, emails case-blind, multi-select values as sets.
Properties a record marks ``create_only`` are written once, when it is created, and never compared again.

References in a record's values are resolved against the portal as it is loaded: a pipeline or stage by label, a
record by key (a line item's product), and :data:`~crm_platform.model.NOW` to the moment of the load.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from crm_platform.hubspot.client import HubSpotClient, batch_failures
from crm_platform.hubspot.schema import PortalState, read_state, type_id
from crm_platform.model import KEY_PROPERTY, NOW, PRIMARY, Record, TenantModel

BATCH = 100
AMOUNT_TOLERANCE = {"amount": 0.011}  # a deal amount HubSpot recalculates from line items, to the cent


@dataclass
class ObjectPlan:
    object_name: str
    creates: list[Record] = field(default_factory=list)
    updates: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)  # (key, hubspot id, changes)
    unchanged: int = 0
    unmanaged: int = 0
    orphans: list[str] = field(default_factory=list)
    duplicates: dict[str, list[str]] = field(default_factory=dict)  # key -> the extra record IDs


@dataclass
class LinkPlan:
    pair: tuple[str, str]
    missing: list[tuple[str, str, str, int]] = field(default_factory=list)  # from id, to id, category, type id
    present: int = 0
    unresolved: list[str] = field(default_factory=list)


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def same(name: str, desired: str, actual: Any) -> bool:
    """Whether a value HubSpot holds already equals the desired one, as HubSpot stores it."""
    have = "" if actual is None else str(actual)
    if desired == have:
        return True
    if not desired or not have:
        return False
    a, b = _float(desired), _float(have)
    if a is not None and b is not None:
        return abs(a - b) <= max(AMOUNT_TOLERANCE.get(name, 0.0), 1e-9 * max(abs(a), abs(b), 1.0))
    for suffix in ("T00:00:00Z", "T00:00:00.000Z"):
        if have.endswith(suffix) and have[: -len(suffix)] == desired:
            return True
    if "@" in desired and desired.lower() == have.lower():
        return True
    if ";" in desired or ";" in have:
        return set(desired.split(";")) == set(have.split(";"))
    return False


class Resolver:
    """Turns reference values into portal IDs, from the state read before the load and the records created in it."""

    def __init__(self, state: PortalState, ids: dict[str, dict[str, str]], now: str) -> None:
        self.state, self.ids, self.now = state, ids, now

    def __call__(self, value: str) -> str:
        if not value.startswith("@"):
            return value
        if value == NOW:
            return self.now
        kind, _, rest = value[1:].partition(":")
        if kind == "record":
            object_name, _, key = rest.partition(":")
            found = self.ids.get(object_name, {}).get(key)
            if found is None:
                raise ValueError(f"{value}: no {object_name} record with that key in the portal or this load")
            return found
        object_name, _, rest = rest.partition(":")
        pipeline_label, _, stage_label = rest.partition(":")
        pipeline = next((p for p in self.state.pipelines.get(object_name, []) if p["label"] == pipeline_label), None)
        if pipeline is None:
            raise ValueError(f"{value}: the portal has no {object_name} pipeline labelled {pipeline_label!r}")
        if kind == "pipeline":
            return str(pipeline["id"])
        stage = next((s for s in pipeline["stages"] if s["label"] == stage_label), None)
        if stage is None:
            raise ValueError(f"{value}: pipeline {pipeline_label!r} has no stage labelled {stage_label!r}")
        return str(stage["id"])


def read_records(client: HubSpotClient, state: PortalState, object_name: str, names: Iterable[str]) -> list[dict]:
    object_type = type_id(state, object_name)
    wanted = ",".join(sorted({KEY_PROPERTY, *names}))
    return list(client.pages(f"/crm/v3/objects/{object_type}?limit=100&archived=false&properties={wanted}"))


def _oldest_first(item: dict) -> tuple[str, int]:
    """Sort key by HubSpot's createdAt, then ID: record IDs are not issued in creation order (seen live)."""
    record_id = str(item["id"])
    return str(item.get("createdAt") or ""), int(record_id) if record_id.isdigit() else 0


def plan_object(object_name: str, desired: list[Record], existing: list[dict],
                resolve: Callable[[str], str]) -> ObjectPlan:
    result = ObjectPlan(object_name)
    by_key: dict[str, dict] = {}
    for item in sorted(existing, key=_oldest_first):
        key = (item.get("properties") or {}).get(KEY_PROPERTY)
        if not key:
            result.unmanaged += 1
        elif key in by_key:  # the oldest record keeps the key; the others are for a person to merge
            result.duplicates.setdefault(key, []).append(str(item["id"]))
        else:
            by_key[key] = item
    wanted = {r.key for r in desired}
    result.orphans = sorted(set(by_key) - wanted)
    for record in desired:
        current = by_key.get(record.key)
        if current is None:
            result.creates.append(record)
            continue
        props = current.get("properties") or {}
        changes = {}
        for name, value in record.properties.items():
            if name in record.create_only:
                continue
            resolved = resolve(value)
            if not same(name, resolved, props.get(name)):
                changes[name] = resolved
        if changes:
            result.updates.append((record.key, str(current["id"]), changes))
        else:
            result.unchanged += 1
    return result


def _label_types(state: PortalState, model: TenantModel, client: HubSpotClient,
                 cache: dict[tuple[str, str], list[dict]], pair: tuple[str, str]) -> list[dict]:
    if pair not in cache:
        cache[pair] = state.labels.get(pair) or client.get(
            f"/crm/v4/associations/{type_id(state, pair[0])}/{type_id(state, pair[1])}/labels")["results"]
    return cache[pair]


def _type_for(model: TenantModel, types: list[dict], pair: tuple[str, str], label: str) -> tuple[str, int] | None:
    if label == PRIMARY:
        match = next((t for t in types if (t.get("label") or "") == "Primary"), None)
    elif not label:
        match = next((t for t in types if not t.get("label")), None)
    else:
        text = next((a.label for a in model.associations if (a.from_object, a.to_object) == pair and a.name == label),
                    None)
        match = next((t for t in types if t.get("label") == text), None) if text else None
    return (str(match["category"]), int(match["typeId"])) if match else None


def plan_links(client: HubSpotClient, state: PortalState, model: TenantModel, records: list[Record],
               ids: dict[str, dict[str, str]]) -> list[LinkPlan]:
    wanted: dict[tuple[str, str], set[tuple[str, str, str]]] = defaultdict(set)
    for record in records:
        for link in record.links:
            wanted[(record.object_name, link.to_object)].add((record.key, link.to_key, link.label))
    cache: dict[tuple[str, str], list[dict]] = {}
    plans = []
    for pair, links in sorted(wanted.items()):
        result = LinkPlan(pair)
        types = _label_types(state, model, client, cache, pair)
        from_ids = sorted({ids[pair[0]][k] for k, _, _ in links if k in ids[pair[0]]})
        have: set[tuple[str, str, int]] = set()
        for start in range(0, len(from_ids), BATCH):
            page = client.post(f"/crm/v4/associations/{type_id(state, pair[0])}/{type_id(state, pair[1])}/batch/read",
                               {"inputs": [{"id": i} for i in from_ids[start:start + BATCH]]})
            for row in page.get("results", []):
                for target in row.get("to", []):
                    for kind in target.get("associationTypes", []):
                        have.add((str(row["from"]["id"]), str(target["toObjectId"]), int(kind["typeId"])))
        for from_key, to_key, label in sorted(links):
            kind = _type_for(model, types, pair, label)
            from_id, to_id = ids[pair[0]].get(from_key), ids[pair[1]].get(to_key)
            if kind is None or from_id is None or to_id is None:
                result.unresolved.append(f"{from_key}->{to_key} {label or 'unlabelled'}")
                continue
            if (from_id, to_id, kind[1]) in have:
                result.present += 1
            else:
                result.missing.append((from_id, to_id, kind[0], kind[1]))
        plans.append(result)
    return plans


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def load(client: HubSpotClient, model: TenantModel, records: list[Record], *, write: bool = True,
         now: Callable[[], str] = _now) -> dict:
    """Plan (and with ``write``, apply) the records and their links; then re-plan to prove convergence."""
    state = read_state(client, model)
    by_object: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        by_object[record.object_name].append(record)
    order = [o.name for o in model.objects if o.name in by_object]
    ids: dict[str, dict[str, str]] = {name: {} for name in order}
    resolve = Resolver(state, ids, now())
    report: dict[str, Any] = {"objects": {}, "links": {}, "failures": []}

    for name in order:
        desired = by_object[name]
        names = {n for r in desired for n in r.properties}
        existing = read_records(client, state, name, names)
        result = plan_object(name, desired, existing, resolve)
        for item in existing:
            key = (item.get("properties") or {}).get(KEY_PROPERTY)
            if key and str(item["id"]) not in result.duplicates.get(key, ()):
                ids[name][key] = str(item["id"])
        report["objects"][name] = {"desired": len(desired), "create": len(result.creates),
                                   "update": len(result.updates), "unchanged": result.unchanged,
                                   "unmanaged": result.unmanaged, "orphans": len(result.orphans),
                                   "duplicates": sum(len(extra) for extra in result.duplicates.values()),
                                   "fields_changed": sorted({f for _, _, c in result.updates for f in c})}
        if not write:
            continue
        object_type = type_id(state, name)
        for start in range(0, len(result.creates), BATCH):
            creates = result.creates[start:start + BATCH]
            inputs = [{"properties": {KEY_PROPERTY: r.key, **{k: resolve(v) for k, v in r.properties.items()}}}
                      for r in creates]
            page = client.post(f"/crm/v3/objects/{object_type}/batch/create", {"inputs": inputs})
            for item in page.get("results", []):
                ids[name][item["properties"][KEY_PROPERTY]] = str(item["id"])
            report["failures"] += [{"object": name, "action": "create", **f} for f in batch_failures(page)]
        for start in range(0, len(result.updates), BATCH):
            updates = result.updates[start:start + BATCH]
            page = client.post(f"/crm/v3/objects/{object_type}/batch/update",
                               {"inputs": [{"id": hs_id, "properties": changes} for _, hs_id, changes in updates]})
            report["failures"] += [{"object": name, "action": "update", **f} for f in batch_failures(page)]

    for link_plan in plan_links(client, state, model, records, ids):
        key = "->".join(link_plan.pair)
        report["links"][key] = {"present": link_plan.present, "create": len(link_plan.missing),
                                "unresolved": len(link_plan.unresolved)}
        if not write:
            continue
        from_type, to_type = type_id(state, link_plan.pair[0]), type_id(state, link_plan.pair[1])
        for start in range(0, len(link_plan.missing), BATCH):
            missing = link_plan.missing[start:start + BATCH]
            page = client.post(f"/crm/v4/associations/{from_type}/{to_type}/batch/create", {"inputs": [
                {"from": {"id": f}, "to": {"id": t}, "types": [{"associationCategory": c, "associationTypeId": i}]}
                for f, t, c, i in missing]})
            report["failures"] += [{"object": key, "action": "associate", **f} for f in batch_failures(page)]
    return report


def converged(report: dict) -> bool:
    """A load report with nothing left to create, update or associate, nothing unresolved and no duplicate keys."""
    return (all(o["create"] == 0 and o["update"] == 0 and not o.get("duplicates") for o in report["objects"].values())
            and all(link["create"] == 0 and link["unresolved"] == 0 for link in report["links"].values())
            and not report["failures"])
