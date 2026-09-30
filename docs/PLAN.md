# HubSpot CRM platform: build plan

Decided with Kush on 2026-09-30:
- **Scope:** the four CRM-shaped projects only.
- **Where:** a new flagship repo.
- **Salesforce:** keep the core CRM-neutral so a Salesforce Developer Edition version can follow.

## Architecture (checked against HubSpot's 2026.09 templates and docs)

- **One private static-auth app per business, all built from shared components.**
  - Static auth installs into a single account.
  - App functions (HubSpot-hosted, Enterprise tier) are available only to static apps. That means no backend to host.
  - The server-side logic runs in app functions. Private functions use `hubspot.serverless('<uid>', {parameters, propertiesToSend})` and read the token from `process.env.PRIVATE_APP_ACCESS_TOKEN`.
  - Workflow-action `actionUrl` and webhook `targetUrl` point at public endpoint functions, `https://<domain>/hs/serverless/api/<uid>`, which need Content Hub Enterprise.
- **Project templates:** HubSpot/hubspot-project-components, folder `2026.09/components` (cards, actions, workflow-actions, webhooks, functions-private, functions-endpoint, settings, pages).
  - `hsproject.json`: `{"name", "srcDir": "src", "platformVersion": "2026.09"}`
  - Each component is a `*-hsmeta.json` file with `uid`, `type` and `config`.
- **HubSpot CLI 8.15.0**, installed locally with `npx hs`:
  - `hs test-account create --name … --sales-level ENTERPRISE …` creates the accounts.
  - `hs project upload`, `hs project install-app`, `hs project validate` and `hs project logs` handle deployment and checks. Validation fetches schemas from the API, so it needs auth.
  - `hs api <path> -X POST --data … --json -a <account>` lets the Python loader reuse the CLI's own auth, so no extra keys are needed.
  - Profiles: `src/hsprofile.<name>.json` holds `{accountId, variables}`, referenced as `${VAR}`.
- **Python `crm_platform`** (stdlib only):
  - `model.py`: a CRM-neutral model covering objects, custom objects, properties and groups, pipelines, and association labels.
  - `hubspot/`: a paced client plus plan, apply and drift for schemas, properties, pipelines and v4 labels.
  - Record reconcile works by a key property, followed by read-back evidence.
  - `salesforce/`: the same model compiled to SFDX metadata XML.

## Tenants

| Business | Source (commit) | CRM data | App components |
|---|---|---|---|
| ScaleLab | growthops-os (portal 247549241, already built, 961 of 1,000 contacts) | existing `growthops_*` properties | Revenue-truth card on contacts; workflow action "advance lifecycle, forward only"; webhooks to an endpoint function that verifies the v3 signature |
| Meridian Supply | cost-to-price-calculator 20fffc4 | 150 companies plus buyers, 240 products (cost, target margin), about 300 deals from the Apr–Jun 2026 quotes (480 lines) | Deal-margin card; "price guardrail" workflow action. Port `pricing/guardrails.py`: floor 12%, target 22%, stretch 30%, approval tiers on the gap to target of 2, 5 and 10 points (Rep, Sales manager, Commercial director, VP Finance) |
| Meat distributor | Customer-Recommendation-Engine 68b10ea | 120 companies plus buyers, 38 products, custom object `recommendation` (top 3 eligible per customer) | Next-best-offer card on companies; functions that create a deal plus line item and accept or dismiss a recommendation |
| AML investigations | aml-transaction-monitoring 9aeecb6 | Custom object `investigation_case` (366 cases with a pipeline and SLA); 90 subjects as contacts and 276 as companies, labelled "Subject"; custom object `counterparty` (286 shared by 2 or more P0/P1 subjects) | Case-evidence card, with the typology catalogue bundled; a function that moves a case to its next stage and creates a task. Stays synthetic; not a compliance product |

## Order of work

1. `scripts/extract_sources.py` builds the snapshots under `data/snapshots/` and a manifest recording commit and sha256 per file.
2. Model, tenants and a fake HubSpot, with tests: plan/apply idempotency, drift, contract, and Salesforce XML.
3. Shared HubSpot components and `scripts/build_hubspot_projects.py`, which writes `hubspot/projects/<tenant>`.
   - Put the business logic in pure JS modules tested with `node --test`.
   - Add a JS-vs-Python guardrail parity test.
4. CI: ruff, mypy, pytest, node tests, tsc, and a gate that fails if generated files change.
5. Live work, which needs Kush's own `npx hs account auth` in the terminal: create 3 test accounts, apply the models, upload and install the apps, add the cards to record views, build the workflows, and capture evidence and screenshots.
6. README, site card (20 projects), profile, résumé and role-fit inventory.
