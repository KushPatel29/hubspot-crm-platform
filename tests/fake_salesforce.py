"""An in-memory Salesforce REST API with the behaviour the loader relies on: SOQL over one object, sObject
Collections create and update with per-record results, a unique external ID, required fields, and the two things
Salesforce owns on an opportunity with products (its Amount, and the refusal to let anyone else set it)."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any
from urllib.parse import unquote

REQUIRED = {"Opportunity": ("Name", "StageName", "CloseDate"), "Account": ("Name",), "Contact": ("LastName",),
            "Product2": ("Name",), "PricebookEntry": ("Pricebook2Id", "Product2Id", "UnitPrice"),
            "OpportunityLineItem": ("OpportunityId", "PricebookEntryId", "Quantity", "UnitPrice")}
UNIQUE_KEY = {"Account", "Contact", "Opportunity"}
KEY = "Crm_Platform_Key__c"


class FakeSalesforce:
    def __init__(self, organization_type: str = "Developer Edition", org_id: str = "00D000000000001") -> None:
        self.organization = {"Id": org_id, "OrganizationType": organization_type, "IsSandbox": False}
        self.book = {"Id": "01s000000000001", "IsActive": False, "IsStandard": True}
        self.objects: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.calls: list[tuple[str, str]] = []
        self.next_id = 0

    def __call__(self, method: str, path: str, body: Any) -> tuple[int, Any, dict[str, str]]:
        self.calls.append((method, path.split("?")[0]))
        if method == "GET" and "/query?q=" in path:
            return 200, {"done": True, "records": self._query(unquote(path.split("?q=", 1)[1]))}, {}
        if path.endswith("/composite/sobjects") and method in ("POST", "PATCH"):
            handler = self._create if method == "POST" else self._update
            return 200, [handler(dict(record)) for record in body["records"]], {}
        return 404, [{"errorCode": "NOT_FOUND", "message": "no such resource"}], {}

    def records(self, sobject: str) -> list[dict[str, Any]]:
        return list(self.objects[sobject].values())

    def by_key(self, sobject: str, key: str) -> dict[str, Any]:
        return next(r for r in self.records(sobject) if r.get(KEY) == key)

    def _query(self, soql: str) -> list[dict[str, Any]]:
        match = re.match(r"SELECT (.+) FROM (\w+)(?: WHERE (.+))?$", soql)
        assert match, soql
        fields, sobject, where = [f.strip() for f in match.group(1).split(",")], match.group(2), match.group(3) or ""
        if sobject == "Organization":
            rows = [self.organization]
        elif sobject == "Pricebook2":
            rows = [self.book]
        else:
            rows = self.records(sobject)
        if where.endswith("!= null"):
            rows = [r for r in rows if r.get(where.split()[0]) is not None]
        elif "=" in where and sobject == "PricebookEntry":
            rows = [r for r in rows if r.get("Pricebook2Id") == where.split("'")[1]]
        return [{"attributes": {"type": sobject}, **{f: r.get(f) for f in fields}} for r in rows]

    @staticmethod
    def _error(code: str, fields: list[str], message: str) -> dict[str, Any]:
        return {"success": False, "errors": [{"statusCode": code, "fields": fields, "message": message}]}

    def _create(self, record: dict[str, Any]) -> dict[str, Any]:
        sobject = record.pop("attributes")["type"]
        missing = [f for f in REQUIRED.get(sobject, ()) if record.get(f) in (None, "")]
        if missing:
            return self._error("REQUIRED_FIELD_MISSING", missing, f"Required fields are missing: {missing}")
        if sobject in UNIQUE_KEY and any(r.get(KEY) == record.get(KEY) for r in self.records(sobject)):
            return self._error("DUPLICATE_VALUE", [KEY], f"duplicate value found: {record.get(KEY)}")
        self.next_id += 1
        record["Id"] = f"{sobject[:3].upper()}{self.next_id:012d}"
        self.objects[sobject][record["Id"]] = record
        self._roll_up(record.get("OpportunityId"))
        return {"id": record["Id"], "success": True, "errors": []}

    def _update(self, record: dict[str, Any]) -> dict[str, Any]:
        sobject = record.pop("attributes")["type"]
        if sobject == "Pricebook2":
            self.book.update(record)
            return {"id": self.book["Id"], "success": True, "errors": []}
        current = self.objects[sobject].get(record["Id"])
        if current is None:
            return self._error("ENTITY_IS_DELETED", [], "entity is deleted")
        lines = [r for r in self.records("OpportunityLineItem") if r["OpportunityId"] == record["Id"]]
        if sobject == "Opportunity" and "Amount" in record and lines:
            return self._error("FIELD_INTEGRITY_EXCEPTION", ["Amount"], "Amount is the sum of its products")
        current.update(record)
        self._roll_up(current.get("OpportunityId"))
        return {"id": current["Id"], "success": True, "errors": []}

    def _roll_up(self, opportunity_id: str | None) -> None:
        if opportunity_id and opportunity_id in self.objects["Opportunity"]:
            lines = [r for r in self.records("OpportunityLineItem") if r["OpportunityId"] == opportunity_id]
            self.objects["Opportunity"][opportunity_id]["Amount"] = round(
                sum(round(r["UnitPrice"] * r["Quantity"], 2) for r in lines), 2)
