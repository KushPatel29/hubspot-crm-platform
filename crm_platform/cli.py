"""Run the platform against a portal: ``python -m crm_platform <command> <tenant> --account <cli account>``.

Commands:

* ``plan``: read-only. What the schema apply would do, and what the record load would create or update.
* ``apply``: apply the schema until it converges, load the records, then re-plan both and record the evidence.
* ``verify``: read-only. Re-plan both and fail unless nothing is left to do.
* ``bind``: record which portal a tenant lives in (``evidence/<tenant>/deploy.json``).
* ``provision-key``: generate the portal's provision key (random, 32 bytes) and store it as the app secret
  ``PROVISION_KEY`` and in the git-ignored ``.secrets/<tenant>.provision.key``. Never printed.

Guards before anything is written:

* The portal must be a developer test account or sandbox (``accountType``), unless ``--allow-portal <id>`` names it.
* Each tenant is bound to exactly one portal. A run whose account resolves to a different portal is refused, so
  the AML cases can never be loaded into the ScaleLab portal because the wrong ``--account`` was typed.

Two ways to reach a portal:

* ``--via cli``: the HubSpot CLI's own login (``npx hs account auth``) through ``scripts/hs_bridge.mjs``. A developer
  key reads the CRM and manages app secrets, but cannot write standard CRM records or their schemas.
* ``--via app`` (the default once a provision key exists): the tenant app's ``provision`` function, which makes
  each call with the app's own token inside HubSpot (``hubspot/functions/provision.js``). No HubSpot credential
  leaves HubSpot; the loader holds only the portal's provision key, which it generated.
Evidence files hold counts, operations and IDs, never record values.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crm_platform.hubspot import records, schema
from crm_platform.hubspot.client import (
    BridgeTransport,
    FunctionTransport,
    HubSpotClient,
    HubSpotError,
    Transport,
    token_transport,
)
from crm_platform.tenants import get

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence"
SECRETS_DIR = ROOT / ".secrets"
TEST_ACCOUNT_TYPES = {"DEVELOPER_TEST", "SANDBOX"}


class Refused(RuntimeError):
    """This run must not touch this portal."""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def deploy_file(tenant: str) -> Path:
    return EVIDENCE / tenant / "deploy.json"


def bound_portal(tenant: str) -> str | None:
    path = deploy_file(tenant)
    return str(json.loads(path.read_text(encoding="utf-8")).get("accountId")) if path.exists() else None


def guard(client: HubSpotClient, tenant: str, *, allow_portal: str | None = None, binding: bool = False) -> dict:
    info = client.get("/account-info/v3/details")
    portal, kind = str(info.get("portalId", "")), str(info.get("accountType", "UNKNOWN"))
    if kind not in TEST_ACCOUNT_TYPES and allow_portal != portal:
        raise Refused(f"portal {portal} is a {kind} account; this platform only writes synthetic data to developer "
                      f"test accounts or sandboxes (pass --allow-portal {portal} if it is meant for it)")
    bound = bound_portal(tenant)
    if bound and bound != portal and not binding:
        raise Refused(f"tenant {tenant} is bound to portal {bound}, but this account is portal {portal}")
    if not bound and not binding:
        raise Refused(f"tenant {tenant} is not bound to a portal yet; run: python -m crm_platform bind {tenant} "
                      "--account <account>")
    return {"portal_id": portal, "account_type": kind, "time_zone": info.get("timeZone"),
            "data_hosting": info.get("dataHostingLocation")}


def endpoint_base_url(client: HubSpotClient, portal_id: str) -> str | None:
    """The portal's system domain, where its public app functions are served (``/hs/serverless/<path>``)."""
    try:
        domains = client.get("/cms/v3/domains").get("results", [])
    except HubSpotError:
        return None
    system = sorted(d["domain"] for d in domains if d.get("domain", "").startswith(f"{portal_id}.hs-sites"))
    return f"https://{system[0]}" if system else None


SECRETS = "/cms/v3/functions/secrets"


def ensure_endpoint_secret(client: HubSpotClient, base: str) -> dict[str, bool]:
    """Set ENDPOINT_BASE_URL (a public URL, not a credential) for the portal's app functions, and report whether
    HUBSPOT_CLIENT_SECRET exists. That one is a credential: a person adds it with ``npx hs secret add``; this
    never reads or writes its value."""
    names = set(client.get(SECRETS).get("results", []))
    body = {"key": "ENDPOINT_BASE_URL", "secret": base}
    client.request("PUT" if "ENDPOINT_BASE_URL" in names else "POST", SECRETS, body)
    return {"ENDPOINT_BASE_URL": True, "HUBSPOT_CLIENT_SECRET": "HUBSPOT_CLIENT_SECRET" in names}


def key_file(tenant: str) -> Path:
    return SECRETS_DIR / f"{tenant}.provision.key"


def provision_key(client: HubSpotClient, tenant: str) -> dict[str, Any]:
    """Generate a fresh provision key, store it as the PROVISION_KEY app secret and locally. Returns no key."""
    key = secrets.token_hex(32)
    names = set(client.get(SECRETS).get("results", []))
    client.request("PUT" if "PROVISION_KEY" in names else "POST", SECRETS, {"key": "PROVISION_KEY", "secret": key})
    path = key_file(tenant)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key, encoding="utf-8")
    return {"tenant": tenant, "stored": "PROVISION_KEY", "local_file": str(path.relative_to(ROOT)), "rotated": True}


def app_transport(tenant: str) -> FunctionTransport:
    deploy = json.loads(deploy_file(tenant).read_text(encoding="utf-8"))
    base = deploy.get("endpointBaseUrl")
    if not base or not key_file(tenant).exists():
        raise Refused(f"tenant {tenant} has no provision endpoint or key yet; run bind and provision-key first")
    return FunctionTransport(f"{base}/hs/serverless/provision", key_file(tenant).read_text(encoding="utf-8").strip())


def write_evidence(tenant: str, name: str, payload: dict) -> Path:
    path = EVIDENCE / tenant / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return path


def _schema_summary(plan: schema.Plan) -> dict:
    return {"operations": schema.describe(plan.ops), "conflicts": schema.describe(plan.conflicts),
            "drift": plan.drift, "missing_requirements": plan.missing_requirements, "converged": plan.converged}


def run(command: str, tenant_key: str, transport: Transport, *, allow_portal: str | None = None) -> dict[str, Any]:
    tenant = get(tenant_key)
    model = tenant.model()
    client = HubSpotClient(transport)
    portal = guard(client, tenant.key, allow_portal=allow_portal, binding=command == "bind")
    if command == "bind":
        path = deploy_file(tenant.key)
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        payload = {**existing, "accountId": int(portal["portal_id"]), "tenant": tenant.key, "boundAt": _now()}
        base = endpoint_base_url(client, portal["portal_id"])
        if base:
            payload["endpointBaseUrl"] = base
            payload["secrets"] = ensure_endpoint_secret(client, base)
        write_evidence(tenant.key, "deploy", payload)
        return {"bound": payload}

    report: dict[str, Any] = {"tenant": tenant.key, "portal": portal, "at": _now()}
    if command == "plan":
        report["schema"] = _schema_summary(schema.plan(model, schema.read_state(client, model)))
        if report["schema"]["converged"]:
            report["records"] = records.load(client, model, tenant.records(), write=False)
        report["writes"] = client.writes
        return report

    if command == "apply":
        report["schema_apply"] = schema.apply(client, model)
        load = records.load(client, model, tenant.records()) if model.objects else {"objects": {}, "links": {},
                                                                                     "failures": []}
        report["load"] = load
        report["writes"] = client.writes

    before = client.writes
    final_schema = schema.plan(model, schema.read_state(client, model))
    report["verify"] = {"schema": _schema_summary(final_schema)}
    if model.objects:
        final = records.load(client, model, tenant.records(), write=False)
        report["verify"]["records"] = final
        report["verify"]["converged"] = final_schema.converged and records.converged(final)
    else:
        report["verify"]["converged"] = final_schema.converged and not final_schema.missing_requirements
    report["verify"]["writes"] = client.writes - before
    report["requests"] = len(client.calls)
    write_evidence(tenant.key, "apply" if command == "apply" else "verify", report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m crm_platform", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("plan", "apply", "verify", "bind", "provision-key"))
    parser.add_argument("tenant")
    parser.add_argument("--account", help="HubSpot CLI account name or ID (npx hs account list)")
    parser.add_argument("--token-env", help="read a bearer token from this environment variable instead")
    parser.add_argument("--allow-portal", help="allow writing to this non-test portal ID")
    parser.add_argument("--via", choices=("app", "cli"), help="reach the portal through the tenant app's provision "
                        "function (default once a provision key exists) or the HubSpot CLI login")
    args = parser.parse_args(argv)
    if args.command == "provision-key":
        if not args.account:
            sys.exit("pass --account <HubSpot CLI account>: the CLI login manages app secrets")
        with BridgeTransport(args.account) as bridge:
            client = HubSpotClient(bridge)
            guard(client, args.tenant, allow_portal=args.allow_portal)
            print(json.dumps(provision_key(client, get(args.tenant).key), indent=2))
        return
    via = args.via or ("app" if key_file(args.tenant).exists() and args.command != "bind" else "cli")
    if via == "app" and not args.token_env:
        report = run(args.command, args.tenant, app_transport(args.tenant), allow_portal=args.allow_portal)
    elif args.token_env:
        token = os.environ.get(args.token_env, "")
        if not token:
            sys.exit(f"{args.token_env} is empty")
        report = run(args.command, args.tenant, token_transport(token), allow_portal=args.allow_portal)
    else:
        if not args.account:
            sys.exit("pass --account <HubSpot CLI account> (or --token-env)")
        with BridgeTransport(args.account) as bridge:
            report = run(args.command, args.tenant, bridge, allow_portal=args.allow_portal)
    print(json.dumps(report, indent=2, sort_keys=True))
    verify = report.get("verify")
    if verify is not None and not verify.get("converged"):
        sys.exit("not converged: see the verify section above")
