"""A stateful stand-in for the HubSpot endpoints this repo calls, strict where HubSpot is strict.

It rejects what the live API rejects and that a test would otherwise miss: a property that does not exist, an
enumeration value that is not an option, a second record with the same unique value, an association type that does
not exist for the pair, a pipeline stage from another pipeline. It also stores values the way HubSpot returns them
(``"12.50"`` comes back ``"12.5"``, a date written to a datetime property comes back at midnight UTC, emails in lower
case), which is what makes "a rerun plans nothing" a meaningful test.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

STANDARD = {"contacts": "0-1", "companies": "0-2", "deals": "0-3", "tickets": "0-5", "products": "0-7",
            "line_items": "0-8"}
SCHEMA_NAMES = {"CONTACT": "0-1", "COMPANY": "0-2", "DEAL": "0-3", "TICKET": "0-5", "PRODUCT": "0-7",
                "LINE_ITEM": "0-8"}


def _prop(name: str, type_: str = "string", field: str = "text") -> dict:
    return {"name": name, "label": name, "type": type_, "fieldType": field, "groupName": "hubspot_defined",
            "description": "", "options": [], "hubspotDefined": True}


STANDARD_PROPERTIES = {
    "0-1": [_prop("email"), _prop("firstname"), _prop("lastname"), _prop("jobtitle"),
            _prop("lifecyclestage", "enumeration", "select")],
    "0-2": [_prop("name"), _prop("domain")],
    "0-3": [_prop("dealname"), _prop("amount", "number", "number"), _prop("closedate", "datetime", "date"),
            _prop("pipeline", "enumeration", "select"), _prop("dealstage", "enumeration", "select")],
    "0-5": [_prop("subject")],
    "0-7": [_prop("name"), _prop("hs_sku"), _prop("price", "number", "number"),
            _prop("hs_cost_of_goods_sold", "number", "number"), _prop("description", "string", "textarea")],
    "0-8": [_prop("name"), _prop("quantity", "number", "number"), _prop("price", "number", "number"),
            _prop("hs_cost_of_goods_sold", "number", "number"), _prop("hs_product_id")],
}
# HubSpot-defined association types for the standard pairs this repo uses: (category, typeId, label)
STANDARD_TYPES = {
    ("0-1", "0-2"): [("HUBSPOT_DEFINED", 279, None), ("HUBSPOT_DEFINED", 1, "Primary")],
    ("0-2", "0-1"): [("HUBSPOT_DEFINED", 280, None), ("HUBSPOT_DEFINED", 2, "Primary")],
    ("0-3", "0-2"): [("HUBSPOT_DEFINED", 341, None), ("HUBSPOT_DEFINED", 5, "Primary")],
    ("0-3", "0-1"): [("HUBSPOT_DEFINED", 3, None)],
    ("0-8", "0-3"): [("HUBSPOT_DEFINED", 20, None)],
    ("0-3", "0-8"): [("HUBSPOT_DEFINED", 19, None)],
}


class FakeHubSpot:
    def __init__(self, portal_id: int = 1234) -> None:
        self.portal_id = portal_id
        self.schemas: list[dict] = []
        self.properties: dict[str, dict[str, dict]] = {t: {p["name"]: copy.deepcopy(p) for p in props}
                                                       for t, props in STANDARD_PROPERTIES.items()}
        self.groups: dict[str, set[str]] = {t: {"hubspot_defined"} for t in STANDARD_PROPERTIES}
        self.pipelines: dict[str, list[dict]] = {"0-3": [{"id": "default", "label": "Sales Pipeline", "stages": [
            {"id": "appointmentscheduled", "label": "Appointment scheduled", "metadata": {"probability": "0.2"}}]}]}
        self.types: dict[tuple[str, str], list[dict]] = {
            pair: [{"category": c, "typeId": i, "label": lab} for c, i, lab in types]
            for pair, types in STANDARD_TYPES.items()}
        self.objects: dict[str, dict[str, dict]] = {}
        self.links: set[tuple[str, str, str, str, int]] = set()  # from type, from id, to type, to id, type id
        self.calls: list[tuple[str, str]] = []
        self._next = 1000
        self.fail_next: list[int] = []  # statuses to answer before handling the next calls (429, 500...)

    # ------------------------------------------------------------------ plumbing
    def _id(self) -> str:
        self._next += 1
        return str(self._next)

    def __call__(self, method: str, path: str, body: Any) -> tuple[int, Any, dict[str, str]]:
        self.calls.append((method, path.split("?")[0]))
        if self.fail_next:
            status = self.fail_next.pop(0)
            return status, {"category": "RATE_LIMITS" if status == 429 else "INTERNAL_ERROR"}, {"Retry-After": "1"}
        body = copy.deepcopy(body)
        url = urlparse(path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        for pattern, handler in self.routes():
            match = re.fullmatch(pattern, f"{method} {url.path}")
            if match:
                try:
                    status, payload = handler(body, query, *match.groups())
                except KeyError as exc:
                    return 404, {"category": "OBJECT_NOT_FOUND", "message": str(exc)}, {}
                return status, json.loads(json.dumps(payload)), {}
        return 404, {"category": "NOT_FOUND", "message": f"no route {method} {url.path}"}, {}

    def routes(self) -> list[tuple[str, Any]]:
        t = r"([0-9]+-[0-9]+|[a-z_]+)"
        return [
            (r"GET /account-info/v3/details", lambda b, q: (200, {"portalId": self.portal_id,
                                                                   "accountType": "DEVELOPER_TEST"})),
            (r"GET /crm/v3/schemas", lambda b, q: (200, {"results": self.schemas})),
            (r"POST /crm/v3/schemas", self.create_schema),
            (rf"POST /crm/v3/schemas/{t}/associations", self.create_schema_association),
            (rf"GET /crm/v3/properties/{t}", lambda b, q, o: (200, {"results": list(self.props(o).values())})),
            (rf"GET /crm/v3/properties/{t}/groups", lambda b, q, o: (200, {"results": [
                {"name": g, "label": g} for g in sorted(self.groups[self.tid(o)])]})),
            (rf"POST /crm/v3/properties/{t}/groups", self.create_group),
            (rf"POST /crm/v3/properties/{t}/batch/create", self.create_properties),
            (rf"PATCH /crm/v3/properties/{t}/([a-z0-9_]+)", self.update_property),
            (rf"GET /crm/v3/pipelines/{t}", lambda b, q, o: (200, {"results": self.pipelines.get(self.tid(o), [])})),
            (rf"POST /crm/v3/pipelines/{t}", self.create_pipeline),
            (rf"POST /crm/v3/pipelines/{t}/([A-Za-z0-9_-]+)/stages", self.create_stage),
            (rf"PATCH /crm/v3/pipelines/{t}/([A-Za-z0-9_-]+)/stages/([A-Za-z0-9_-]+)", self.update_stage),
            (rf"GET /crm/v4/associations/{t}/{t}/labels", self.get_labels),
            (rf"POST /crm/v4/associations/{t}/{t}/labels", self.create_label),
            (rf"POST /crm/v4/associations/{t}/{t}/batch/read", self.read_links),
            (rf"POST /crm/v4/associations/{t}/{t}/batch/create", self.create_links),
            (rf"GET /crm/v3/objects/{t}", self.list_objects),
            (rf"GET /crm/v3/objects/{t}/([0-9]+)", self.get_object),
            (rf"POST /crm/v3/objects/{t}/batch/create", self.create_objects),
            (rf"POST /crm/v3/objects/{t}/batch/update", self.update_objects),
            (rf"PATCH /crm/v3/objects/{t}/([0-9]+)", self.patch_object),
        ]

    def tid(self, object_type: str) -> str:
        if object_type in STANDARD:
            return STANDARD[object_type]
        if re.fullmatch(r"[0-9]+-[0-9]+", object_type):
            if object_type not in self.properties:
                raise KeyError(object_type)
            return object_type
        for schema in self.schemas:
            if object_type in (schema["name"], schema["fullyQualifiedName"]):
                return schema["objectTypeId"]
        raise KeyError(object_type)

    def props(self, object_type: str) -> dict[str, dict]:
        return self.properties[self.tid(object_type)]

    # ------------------------------------------------------------------ schema
    def create_schema(self, body: dict, query: dict) -> tuple[int, Any]:
        if any(s["name"] == body["name"] for s in self.schemas):
            return 409, {"category": "OBJECT_ALREADY_EXISTS"}
        object_type = f"2-{self._id()}"
        group = f"{body['name']}_information"
        schema = {"id": object_type[2:], "objectTypeId": object_type, "name": body["name"],
                  "fullyQualifiedName": f"p{self.portal_id}_{body['name']}", "labels": body["labels"],
                  "primaryDisplayProperty": body["primaryDisplayProperty"], "associations": []}
        self.schemas.append(schema)
        self.properties[object_type] = {"hs_object_id": _prop("hs_object_id", "number", "number"),
                                        "hs_pipeline": _prop("hs_pipeline", "enumeration", "select"),
                                        "hs_pipeline_stage": _prop("hs_pipeline_stage", "enumeration", "select")}
        self.groups[object_type] = {group}
        names = {p["name"] for p in body["properties"]}
        for needed in [body["primaryDisplayProperty"], *body.get("requiredProperties", [])]:
            if needed not in names:
                self.schemas.pop()
                return 400, {"category": "VALIDATION_ERROR", "context": {"propertyName": [needed]}}
        for prop in body["properties"]:
            self.properties[object_type][prop["name"]] = {**prop, "groupName": group, "description":
                                                          prop.get("description", "")}
        for other in body.get("associatedObjects", []):
            self._define_pair(object_type, SCHEMA_NAMES[other], schema)
        return 201, schema

    def _define_pair(self, a: str, b: str, schema: dict) -> None:
        forward, backward = int(self._id()), int(self._id())
        self.types.setdefault((a, b), []).append({"category": "USER_DEFINED", "typeId": forward, "label": None})
        self.types.setdefault((b, a), []).append({"category": "USER_DEFINED", "typeId": backward, "label": None})
        schema["associations"].append({"fromObjectTypeId": a, "toObjectTypeId": b, "id": str(forward)})

    def create_schema_association(self, body: dict, query: dict, object_type: str) -> tuple[int, Any]:
        schema = next(s for s in self.schemas if s["objectTypeId"] == self.tid(object_type))
        self._define_pair(body["fromObjectTypeId"], body["toObjectTypeId"], schema)
        return 201, {"id": schema["associations"][-1]["id"]}

    def create_group(self, body: dict, query: dict, object_type: str) -> tuple[int, Any]:
        groups = self.groups[self.tid(object_type)]
        if body["name"] in groups:
            return 409, {"category": "OBJECT_ALREADY_EXISTS"}
        groups.add(body["name"])
        return 201, body

    def _check_property(self, object_type: str, body: dict) -> str:
        if body.get("groupName") not in self.groups[object_type]:
            return "group"
        if body["type"] in ("enumeration", "bool") and not body.get("options"):
            return "options"
        return ""

    def create_properties(self, body: dict, query: dict, object_type: str) -> tuple[int, Any]:
        tid = self.tid(object_type)
        results, errors = [], []
        for prop in body["inputs"]:
            problem = self._check_property(tid, prop)
            if prop["name"] in self.properties[tid]:
                errors.append({"category": "OBJECT_ALREADY_EXISTS", "context": {"propertyName": [prop["name"]]}})
            elif problem:
                errors.append({"category": "VALIDATION_ERROR", "context": {"propertyName": [prop["name"]]}})
            else:
                stored = {"description": "", "options": [], **prop}
                self.properties[tid][prop["name"]] = stored
                results.append(stored)
        return (207 if errors else 201), {"results": results, "errors": errors}

    def update_property(self, body: dict, query: dict, object_type: str, name: str) -> tuple[int, Any]:
        current = self.props(object_type)[name]
        if "type" in body or "hasUniqueValue" in body:
            return 400, {"category": "VALIDATION_ERROR"}
        if "groupName" in body and body["groupName"] not in self.groups[self.tid(object_type)]:
            return 400, {"category": "VALIDATION_ERROR"}
        current.update(body)
        return 200, current

    # ------------------------------------------------------------------ pipelines and labels
    def _stage(self, body: dict) -> dict:
        return {"id": self._id(), "label": body["label"], "displayOrder": body.get("displayOrder", 0),
                "metadata": {k: str(v) for k, v in (body.get("metadata") or {}).items()}}

    def create_pipeline(self, body: dict, query: dict, object_type: str) -> tuple[int, Any]:
        pipeline = {"id": self._id(), "label": body["label"], "stages": [self._stage(s) for s in body["stages"]]}
        self.pipelines.setdefault(self.tid(object_type), []).append(pipeline)
        return 201, pipeline

    def create_stage(self, body: dict, query: dict, object_type: str, pipeline_id: str) -> tuple[int, Any]:
        pipeline = next(p for p in self.pipelines[self.tid(object_type)] if p["id"] == pipeline_id)
        stage = self._stage(body)
        pipeline["stages"].append(stage)
        return 201, stage

    def update_stage(self, body: dict, query: dict, object_type: str, pipeline_id: str, stage_id: str
                     ) -> tuple[int, Any]:
        pipeline = next(p for p in self.pipelines[self.tid(object_type)] if p["id"] == pipeline_id)
        stage = next(s for s in pipeline["stages"] if s["id"] == stage_id)
        stage["metadata"] = {k: str(v) for k, v in body.get("metadata", {}).items()}
        return 200, stage

    def get_labels(self, body: Any, query: dict, a: str, b: str) -> tuple[int, Any]:
        return 200, {"results": self.types.get((self.tid(a), self.tid(b)), [])}

    def create_label(self, body: dict, query: dict, a: str, b: str) -> tuple[int, Any]:
        pair = (self.tid(a), self.tid(b))
        if pair not in self.types:
            return 400, {"category": "VALIDATION_ERROR", "message": "objects cannot be associated"}
        forward = {"category": "USER_DEFINED", "typeId": int(self._id()), "label": body["label"]}
        self.types[pair].append(forward)
        backward = {"category": "USER_DEFINED", "typeId": int(self._id()),
                    "label": body.get("inverseLabel", body["label"])}
        self.types.setdefault(pair[::-1], []).append(backward)
        return 200, {"results": [forward, backward]}

    # ------------------------------------------------------------------ records
    def _normalise(self, tid: str, name: str, value: Any) -> str:
        prop = self.properties[tid].get(name)
        if prop is None:
            raise ValueError(name)
        text = "" if value is None else str(value)
        if not text:
            return text
        if prop["type"] == "number":
            number = float(text)
            return str(int(number)) if number.is_integer() else repr(number)
        if prop["type"] == "datetime" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return text + "T00:00:00Z"
        if prop["type"] == "enumeration" and name not in ("pipeline", "dealstage", "hs_pipeline", "hs_pipeline_stage"):
            allowed = {o["value"] for o in prop.get("options") or []}
            parts = text.split(";") if prop["fieldType"] == "checkbox" else [text]
            if any(part not in allowed for part in parts):
                raise ValueError(name)
        if name == "email":
            return text.lower()
        return text

    def _write(self, tid: str, record_id: str | None, properties: dict) -> tuple[dict | None, dict | None]:
        store = self.objects.setdefault(tid, {})
        record = store.get(record_id) if record_id else None
        props = dict(record["properties"]) if record else {}
        try:
            for name, value in properties.items():
                props[name] = self._normalise(tid, name, value)
        except ValueError as exc:
            return None, {"category": "VALIDATION_ERROR", "context": {"propertyName": [str(exc)]}}
        for name, value in props.items():
            unique = self.properties[tid].get(name, {}).get("hasUniqueValue")
            if unique and value and any(other["properties"].get(name) == value and other["id"] != record_id
                                        for other in self.objects.get(tid, {}).values()):
                return None, {"category": "VALIDATION_ERROR", "message": "duplicate unique value",
                              "context": {"propertyName": [name]}}
        stage_error = self._check_stage(tid, props)
        if stage_error:
            return None, stage_error
        if record is None:
            record_id = self._id()
            record = {"id": record_id, "properties": {}, "archived": False}
            self.objects[tid][record_id] = record
        record["properties"] = props
        record["properties"]["hs_object_id"] = record["id"]
        return record, None

    def _check_stage(self, tid: str, props: dict) -> dict | None:
        pipeline_key, stage_key = ("pipeline", "dealstage") if tid == "0-3" else ("hs_pipeline", "hs_pipeline_stage")
        if stage_key not in props:
            return None
        pipeline = next((p for p in self.pipelines.get(tid, []) if p["id"] == props.get(pipeline_key)), None)
        if pipeline is None or props[stage_key] not in {s["id"] for s in pipeline["stages"]}:
            return {"category": "VALIDATION_ERROR", "context": {"propertyName": [stage_key]}}
        return None

    def create_objects(self, body: dict, query: dict, object_type: str) -> tuple[int, Any]:
        tid = self.tid(object_type)
        results, errors = [], []
        for item in body["inputs"]:
            record, problem = self._write(tid, None, item["properties"])
            (results.append(record) if record else errors.append(problem))
        return (207 if errors else 201), {"results": results, "errors": errors}

    def update_objects(self, body: dict, query: dict, object_type: str) -> tuple[int, Any]:
        tid = self.tid(object_type)
        results, errors = [], []
        for item in body["inputs"]:
            if item["id"] not in self.objects.get(tid, {}):
                errors.append({"category": "OBJECT_NOT_FOUND", "context": {"ids": [item["id"]]}})
                continue
            record, problem = self._write(tid, item["id"], item["properties"])
            (results.append(record) if record else errors.append({**problem, "context": {
                **problem.get("context", {}), "ids": [item["id"]]}}))
        return (207 if errors else 200), {"results": results, "errors": errors}

    def patch_object(self, body: dict, query: dict, object_type: str, record_id: str) -> tuple[int, Any]:
        record, problem = self._write(self.tid(object_type), record_id, body["properties"])
        return (200, record) if record else (400, problem)

    def _view(self, record: dict, wanted: list[str]) -> dict:
        return {"id": record["id"], "properties": {k: record["properties"].get(k) for k in wanted},
                "archived": False}

    def list_objects(self, body: Any, query: dict, object_type: str) -> tuple[int, Any]:
        tid = self.tid(object_type)
        wanted = [p for p in query.get("properties", "").split(",") if p]
        records = sorted(self.objects.get(tid, {}).values(), key=lambda r: int(r["id"]))
        start = int(query.get("after", 0))
        limit = int(query.get("limit", 10))
        page = [self._view(r, wanted) for r in records[start:start + limit]]
        payload: dict = {"results": page}
        if start + limit < len(records):
            payload["paging"] = {"next": {"after": str(start + limit)}}
        return 200, payload

    def get_object(self, body: Any, query: dict, object_type: str, record_id: str) -> tuple[int, Any]:
        record = self.objects[self.tid(object_type)][record_id]
        wanted = [p for p in query.get("properties", "").split(",") if p] or list(record["properties"])
        return 200, self._view(record, wanted)

    def read_links(self, body: dict, query: dict, a: str, b: str) -> tuple[int, Any]:
        ta, tb = self.tid(a), self.tid(b)
        results = []
        for item in body["inputs"]:
            targets: dict[str, list[dict]] = {}
            for fa, fid, fb, tid, type_id in self.links:
                if (fa, fid, fb) == (ta, item["id"], tb):
                    label = next(t for t in self.types[(ta, tb)] if t["typeId"] == type_id)
                    targets.setdefault(tid, []).append(label)
            if targets:
                results.append({"from": {"id": item["id"]}, "to": [
                    {"toObjectId": int(t), "associationTypes": types} for t, types in sorted(targets.items())]})
        return 200, {"results": results}

    def create_links(self, body: dict, query: dict, a: str, b: str) -> tuple[int, Any]:
        ta, tb = self.tid(a), self.tid(b)
        errors = []
        for item in body["inputs"]:
            for kind in item["types"]:
                valid = {(t["category"], t["typeId"]) for t in self.types.get((ta, tb), [])}
                exists = (item["from"]["id"] in self.objects.get(ta, {})
                          and item["to"]["id"] in self.objects.get(tb, {}))
                if (kind["associationCategory"], kind["associationTypeId"]) not in valid or not exists:
                    errors.append({"category": "VALIDATION_ERROR", "context": {"ids": [item["from"]["id"]]}})
                    continue
                self.links.add((ta, item["from"]["id"], tb, item["to"]["id"], kind["associationTypeId"]))
                inverse = self._inverse(ta, tb, kind["associationTypeId"])
                if inverse is not None:
                    self.links.add((tb, item["to"]["id"], ta, item["from"]["id"], inverse))
        return (207 if errors else 201), {"results": [], "errors": errors}

    def _inverse(self, ta: str, tb: str, type_id: int) -> int | None:
        forward = self.types[(ta, tb)].index(next(t for t in self.types[(ta, tb)] if t["typeId"] == type_id))
        backward = self.types.get((tb, ta), [])
        return backward[forward]["typeId"] if forward < len(backward) else None

    # ------------------------------------------------------------------ helpers for tests
    def records(self, object_type: str) -> list[dict]:
        return list(self.objects.get(self.tid(object_type), {}).values())

    def by_key(self, object_type: str, key: str) -> dict:
        return next(r for r in self.records(object_type) if r["properties"].get("crm_platform_key") == key)
