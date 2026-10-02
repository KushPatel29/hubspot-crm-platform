"""Load a tenant's records into a Salesforce org by the same key, with the same promise as the HubSpot loader.

``python -m crm_platform.salesforce.load meridian --org <alias>`` reads what the org holds, matches it to the model's
records by ``Crm_Platform_Key__c`` (the external ID the generated metadata creates), and plans creates and field
updates; ``--write`` applies them and plans again. Nothing is deleted. Values are compared the way the HubSpot loader
compares them, so a rerun plans nothing.

That second plan is also the cross-system check on the guardrail. The loader writes each opportunity's verdict,
approver, blended margin and margin given up as the *Python* guardrail computed them; inserting the line items then
fires the Apex trigger, which rescores every opportunity with the *Apex* guardrail. If the two disagreed anywhere, the
next plan would want to put a value back. It plans zero.

What maps to what (the field side of ``crm_platform/salesforce/metadata.py``):

=====================  ================================================================================
Model                  Salesforce
=====================  ================================================================================
companies              Account: ``name`` → Name, ``domain`` → Website
contacts               Contact: names, Email, ``jobtitle`` → Title; its company link → AccountId
products               Product2 (active): Name, ``hs_sku`` → ProductCode, ``hs_cost_of_goods_sold`` →
                       Unit_Cost__c; ``price`` → a PricebookEntry in the standard price book
deals                  Opportunity on the standard price book: Name, CloseDate, the stage *label* as
                       StageName, the company link → AccountId; Amount on create only (Salesforce then
                       owns it: it is the sum of the lines)
line_items             OpportunityLineItem: Quantity, UnitPrice, Unit_Cost__c, the product's price-book entry
any other property     ``<Title_Case>__c``, numbers as numbers
=====================  ================================================================================

Authentication is the Salesforce CLI's: ``sf org display`` hands this process the org's instance URL and
``sf org auth show-access-token`` a session token, which stay in memory and are never printed. Only Developer
Edition orgs, sandboxes and scratch orgs are written to, and a tenant stays bound to the first org it is loaded
into.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from crm_platform.hubspot.client import Transport, token_transport
from crm_platform.hubspot.records import same
from crm_platform.model import KEY_PROPERTY, NOW, PRIMARY, Record, TenantModel
from crm_platform.salesforce.metadata import API_VERSION, HAND_WRITTEN, UNIT_COST, api_name, object_api
from crm_platform.tenants import TENANTS

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "evidence"
API = f"/services/data/v{API_VERSION}"
KEY = f"{api_name(KEY_PROPERTY)}__c"
BATCH = 200
STANDARD_FIELDS: dict[str, dict[str, str | None]] = {
    "companies": {"name": "Name", "domain": "Website"},
    "contacts": {"firstname": "FirstName", "lastname": "LastName", "email": "Email", "jobtitle": "Title"},
    "products": {"name": "Name", "hs_sku": "ProductCode", "description": "Description",
                 "hs_cost_of_goods_sold": UNIT_COST, "price": None},  # price: the standard price-book entry
    "deals": {"dealname": "Name", "amount": "Amount", "closedate": "CloseDate", "dealstage": "StageName",
              "pipeline": None},  # the sales process is metadata, not a field value
    "line_items": {"quantity": "Quantity", "price": "UnitPrice", "hs_cost_of_goods_sold": UNIT_COST,
                   "name": None, "hs_product_id": None},  # the product arrives as its price-book entry
}
NUMERIC_STANDARD = {"Amount", "UnitPrice", "Quantity", UNIT_COST}
# Set when the record is created and Salesforce's afterwards: an opportunity's Amount is the sum of its lines.
SALESFORCE_OWNED = {"deals": frozenset({"amount"})}
PARENT_FIELDS = {("contacts", "companies"): "AccountId", ("deals", "companies"): "AccountId",
                 ("line_items", "deals"): "OpportunityId"}
WRITABLE_ORGS = ("Developer Edition",)


class SalesforceError(RuntimeError):
    """A failed Salesforce call. Safe to log: the error codes and field names, never the message (it echoes values)."""

    def __init__(self, status: int, method: str, path: str, codes: tuple[str, ...] = (), fields: tuple[str, ...] = ()):
        self.status, self.codes, self.fields = status, codes, fields
        detail = "; ".join(part for part in (", ".join(codes), f"fields {', '.join(fields)}" if fields else "") if part)
        super().__init__(f"salesforce: HTTP {status} {method} {path.split('?')[0]}" + (f": {detail}" if detail else ""))


def error_parts(body: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    items = body if isinstance(body, list) else [body] if isinstance(body, dict) else []
    codes = tuple(sorted({str(i.get("errorCode") or i.get("statusCode") or "") for i in items if isinstance(i, dict)}
                         - {""}))
    fields = tuple(sorted({f for i in items if isinstance(i, dict) for f in (i.get("fields") or [])}))
    return codes, fields


class SalesforceClient:
    """REST over a transport that owns the session: retried on 429/5xx/network, errors redacted."""

    def __init__(self, transport: Transport, *, sleep: Callable[[float], None] = time.sleep, retries: int = 3) -> None:
        self.transport, self.sleep, self.retries = transport, sleep, retries
        self.writes = 0
        self.requests = 0

    def call(self, method: str, path: str, body: Any = None) -> Any:
        status: int = 0
        parsed: Any = None
        for attempt in range(self.retries):
            self.requests += 1
            status, parsed, _ = self.transport(method, path, body)
            if status not in (0, 429) and status < 500:
                break
            self.sleep(min(8.0, 0.5 * 2 ** attempt))
        if not 200 <= status < 300:
            raise SalesforceError(status, method, path, *error_parts(parsed))
        if method != "GET":
            self.writes += 1
        return parsed

    def query(self, soql: str) -> list[dict]:
        page = self.call("GET", f"{API}/query?q={quote(soql)}")
        rows = list(page["records"])
        while not page.get("done", True):
            page = self.call("GET", page["nextRecordsUrl"])
            rows += page["records"]
        return rows

    def _collection(self, method: str, sobject: str, rows: list[dict]) -> list[dict]:
        results: list[dict] = []
        for start in range(0, len(rows), BATCH):
            records = [{"attributes": {"type": sobject}, **row} for row in rows[start:start + BATCH]]
            results += self.call(method, f"{API}/composite/sobjects", {"allOrNone": False, "records": records})
        return results

    def create(self, sobject: str, rows: list[dict]) -> list[dict]:
        return self._collection("POST", sobject, rows)

    def update(self, sobject: str, rows: list[dict]) -> list[dict]:
        return self._collection("PATCH", sobject, rows)


def sobject_for(model: TenantModel, object_name: str) -> str:
    return object_api(model, object_name)


def field_for(model: TenantModel, object_name: str, prop: str) -> str | None:
    standard = STANDARD_FIELDS.get(object_name, {})
    if prop in standard:
        return standard[prop]
    if prop.startswith("hs_"):
        return None  # a HubSpot-internal property with no Salesforce home
    return f"{api_name(prop)}__c"


def _numeric(model: TenantModel, object_name: str, prop: str, field: str) -> bool:
    if field in NUMERIC_STANDARD:
        return True
    obj = model.object(object_name)
    return any(p.name == prop and p.type == "number" for p in obj.properties)


def value_for(value: str, now: str) -> str | None:
    """A model value as Salesforce takes it: a stage reference becomes the stage's label."""
    if value == NOW:
        return now
    if value.startswith("@stage:"):
        return value.rsplit(":", 1)[1]
    if value.startswith("@"):
        return None
    return value


def desired_row(model: TenantModel, record: Record, now: str) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for prop, raw in record.properties.items():
        field = field_for(model, record.object_name, prop)
        value = value_for(raw, now)
        if field is None or value is None:
            continue
        if _numeric(model, record.object_name, prop, field):
            row[field] = float(value) if value != "" else None
        else:
            row[field] = value
    return row


def _failures(sobject: str, action: str, results: list[dict]) -> list[dict]:
    out = []
    for result in results:
        if not result.get("success"):
            codes, fields = error_parts(result.get("errors") or [])
            out.append({"object": sobject, "action": action, "codes": list(codes), "fields": list(fields)})
    return out


def org_guard(client: SalesforceClient, tenant: str, *, binding: bool) -> dict[str, str]:
    """Refuse anything but a Developer Edition org or a sandbox, and any org but the one the tenant is bound to."""
    org = client.query("SELECT Id, OrganizationType, IsSandbox FROM Organization")[0]
    if org["OrganizationType"] not in WRITABLE_ORGS and not org["IsSandbox"]:
        raise SystemExit(f"refusing to load into a {org['OrganizationType']} org: Developer Edition or a sandbox only")
    bound = EVIDENCE / tenant / "salesforce_org.json"
    if bound.exists():
        expected = json.loads(bound.read_text(encoding="utf-8"))["orgId"]
        if expected != org["Id"]:
            raise SystemExit(f"{tenant} is bound to Salesforce org {expected}; this login is {org['Id']}")
    elif binding:
        bound.parent.mkdir(parents=True, exist_ok=True)
        bound.write_text(json.dumps({"orgId": org["Id"], "organizationType": org["OrganizationType"],
                                     "tenant": tenant}, indent=2) + "\n", encoding="utf-8", newline="\n")
    return {"org_id": org["Id"], "organization_type": org["OrganizationType"], "sandbox": str(org["IsSandbox"]).lower()}


def load(client: SalesforceClient, model: TenantModel, records: list[Record], *, write: bool = False,
         now: Callable[[], str] = lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")) -> dict[str, Any]:
    """Plan (and with ``write``, apply) the records. The report holds counts and field names, never values."""
    stamp = now()
    by_object: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        by_object[record.object_name].append(record)
    order = [o.name for o in model.objects if o.name in by_object]
    ids: dict[str, dict[str, str]] = {name: {} for name in order}
    report: dict[str, Any] = {"objects": {}, "price_book": {}, "failures": []}

    book = client.query("SELECT Id, IsActive FROM Pricebook2 WHERE IsStandard = true")[0]
    if not book["IsActive"] and write:
        report["failures"] += _failures("Pricebook2", "activate",
                                        client.update("Pricebook2", [{"Id": book["Id"], "IsActive": True}]))
    entries: dict[str, str] = {}  # product key -> price-book entry ID

    for name in order:
        sobject = sobject_for(model, name)
        desired = {r.key: desired_row(model, r, stamp) for r in by_object[name]}
        fields = sorted({f for row in desired.values() for f in row})
        existing = {row[KEY]: row for row in client.query(
            f"SELECT Id, {KEY}{''.join(', ' + f for f in fields)} FROM {sobject} WHERE {KEY} != null")}
        ids[name].update({key: row["Id"] for key, row in existing.items()})
        creates, updates, changed_fields, unchanged = [], [], set(), 0
        for record in by_object[name]:
            row = desired[record.key]
            current = existing.get(record.key)
            if current is None:
                creates.append(record)
                continue
            frozen = record.create_only | SALESFORCE_OWNED.get(name, frozenset())
            skip = {field_for(model, name, prop) for prop in frozen}
            changes = {f: v for f, v in row.items() if f not in skip
                       and not same(f, "" if v is None else _text(v), current.get(f))}
            if changes:
                updates.append({"Id": current["Id"], **changes})
                changed_fields |= set(changes)
            else:
                unchanged += 1
        report["objects"][name] = {"sobject": sobject, "desired": len(desired), "create": len(creates),
                                   "update": len(updates), "unchanged": unchanged,
                                   "orphans": len(set(existing) - set(desired)),
                                   "fields_changed": sorted(changed_fields)}
        if write:
            rows, keys = [], []
            for record in creates:
                row = {KEY: record.key, **desired[record.key], **_parents(name, record, ids, entries, book["Id"])}
                if name == "line_items" and "PricebookEntryId" not in row:
                    report["failures"].append({"object": sobject, "action": "create", "codes": ["NO_PRICE_BOOK_ENTRY"],
                                               "fields": []})
                    continue
                rows.append(row)
                keys.append(record.key)
            results = client.create(sobject, rows)
            for key, result in zip(keys, results, strict=True):
                if result.get("success"):
                    ids[name][key] = result["id"]
            report["failures"] += _failures(sobject, "create", results)
            report["failures"] += _failures(sobject, "update", client.update(sobject, updates))
        if name == "products":
            report["price_book"] = _price_book(client, by_object[name], ids[name], book["Id"], entries, write, report)
    return report


def _text(value: Any) -> str:
    if isinstance(value, float):
        return repr(value) if value != int(value) else str(int(value))
    return str(value)


def _parents(name: str, record: Record, ids: dict[str, dict[str, str]], entries: dict[str, str],
             book_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for link in record.links:
        field = PARENT_FIELDS.get((name, link.to_object))
        parent = ids.get(link.to_object, {}).get(link.to_key)
        if field and parent and (link.label in ("", PRIMARY) or field not in out):
            out[field] = parent
    if name == "products":
        out["IsActive"] = True
    if name == "deals":
        out["Pricebook2Id"] = book_id
    if name == "line_items":
        product = record.properties.get("hs_product_id", "").rsplit(":", 1)[-1]
        if product in entries:
            out["PricebookEntryId"] = entries[product]
    return out


def _price_book(client: SalesforceClient, products: list[Record], product_ids: dict[str, str], book_id: str,
                entries: dict[str, str], write: bool, report: dict[str, Any]) -> dict[str, int]:
    """One entry per product in the standard price book, at the product's price."""
    by_product = {row["Product2Id"]: row for row in client.query(
        f"SELECT Id, Product2Id, UnitPrice FROM PricebookEntry WHERE Pricebook2Id = '{book_id}'")}
    creates, updates, unchanged, keys = [], [], 0, []
    for record in products:
        product_id = product_ids.get(record.key)
        price = float(record.properties["price"])
        current = by_product.get(product_id) if product_id else None
        if current is not None:
            entries[record.key] = current["Id"]
            if same("UnitPrice", _text(price), current.get("UnitPrice")):
                unchanged += 1
            else:
                updates.append({"Id": current["Id"], "UnitPrice": price})
        elif product_id:
            creates.append({"Pricebook2Id": book_id, "Product2Id": product_id, "UnitPrice": price, "IsActive": True})
            keys.append(record.key)
    summary = {"desired": len(products), "create": len(creates) + sum(1 for r in products if r.key not in product_ids),
               "update": len(updates), "unchanged": unchanged}
    if write:
        results = client.create("PricebookEntry", creates)
        for key, result in zip(keys, results, strict=True):
            if result.get("success"):
                entries[key] = result["id"]
        report["failures"] += _failures("PricebookEntry", "create", results)
        report["failures"] += _failures("PricebookEntry", "update", client.update("PricebookEntry", updates))
    return summary


def converged(report: dict[str, Any]) -> bool:
    plans = [*report["objects"].values(), report["price_book"]] if report["price_book"] else report["objects"].values()
    return all(p["create"] == 0 and p["update"] == 0 for p in plans) and not report["failures"]


def cli_session(ask: Callable[[list[str]], dict[str, Any]], alias: str) -> tuple[str, str]:
    """``(access token, instance URL)`` for ``alias``, from the CLI's answers to ``ask``.

    ``sf org display`` used to carry the token. Since CLI 2.15x it carries "[REDACTED] Use 'sf org auth
    show-access-token' to view" in the same field, which an org answers with INVALID_AUTH_HEADER, so anything
    that is not a token (it has a space, or is missing) is fetched from that command instead.
    """
    shown = ask(["org", "display", "--target-org", alias, "--json"])
    token = shown.get("accessToken") or ""
    if not token or " " in token:
        token = ask(["org", "auth", "show-access-token", "--target-org", alias, "--json"])["accessToken"]
    return token, shown["instanceUrl"]


def cli_transport(alias: str) -> Transport:
    """The Salesforce CLI's session for ``alias``: instance URL and token read from the CLI, kept in memory, never
    printed."""
    binary = shutil.which("sf") or shutil.which("sf.cmd")
    local = ROOT / "node_modules" / ".bin" / ("sf.cmd" if sys.platform == "win32" else "sf")
    command = [str(local)] if local.exists() else [binary] if binary else None
    if command is None:
        raise SystemExit("the Salesforce CLI is not installed: npm install (it is a devDependency), then "
                         "npx sf org login web --alias <alias>")

    def ask(args: list[str]) -> dict[str, Any]:
        done = subprocess.run([*command, *args], capture_output=True, text=True, check=False, cwd=ROOT)
        return json.loads(done.stdout)["result"]

    try:
        token, base_url = cli_session(ask, alias)
        return token_transport(token, base_url=base_url)
    except (ValueError, KeyError, TypeError):
        raise SystemExit(f"no Salesforce login for {alias!r}: npx sf org login web --alias {alias}") from None


def run(tenant_key: str, transport: Transport, *, write: bool) -> dict[str, Any]:
    tenant = TENANTS[tenant_key]
    model = tenant.model()
    client = SalesforceClient(transport)
    org = org_guard(client, tenant_key, binding=write)
    records = tenant.records()
    report: dict[str, Any] = {"tenant": tenant_key, "org": org,
                              "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    report["load"] = load(client, model, records, write=write)
    if write:
        writes = client.writes
        verify = load(SalesforceClient(transport), model, records, write=False)
        report["verify"] = {"converged": converged(verify), "objects": verify["objects"],
                            "price_book": verify["price_book"], "writes": 0}
        report["writes"] = writes
        report["requests"] = client.requests
    else:
        report["converged"] = converged(report["load"])
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("tenant", choices=sorted(HAND_WRITTEN))
    parser.add_argument("--org", required=True, help="the Salesforce CLI alias of a Developer Edition org or sandbox")
    parser.add_argument("--write", action="store_true", help="apply the plan, then plan again")
    args = parser.parse_args(argv)
    report = run(args.tenant, cli_transport(args.org), write=args.write)
    if args.write:
        path = EVIDENCE / args.tenant / "salesforce_load.json"
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    ok = report["verify"]["converged"] if args.write else True
    sys.exit(0 if ok and not report["load"]["failures"] else 1)


if __name__ == "__main__":
    main()
