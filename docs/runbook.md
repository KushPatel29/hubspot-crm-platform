# Runbook: standing up a business's CRM in HubSpot

Every step is a command; the only things a person types are credentials, into HubSpot's own prompts. Nothing here
asks for a key in a file, a chat or an environment variable that gets committed.

## 0. Once per machine

```bash
npm install                      # the HubSpot CLI, pinned in package.json
npm install --prefix hubspot     # the apps workspace: cards, functions, tests, generator
python -m pip install -e . -r requirements-dev.txt
npx hs account auth              # paste a personal access key into the CLI's prompt
```

The Python tools reach HubSpot through that login (`scripts/hs_bridge.mjs` uses the CLI's own library, which swaps
the key for short-lived tokens), so they hold no key of their own.

## 1. A portal per business

Each business gets its own developer test account on Enterprise tiers (custom objects, app functions and public
endpoints need Enterprise):

```bash
npx hs test-account create --name "Meridian Supply" --sales-level ENTERPRISE --service-level ENTERPRISE \
  --marketing-level ENTERPRISE --content-level ENTERPRISE --ops-level ENTERPRISE
```

ScaleLab already exists (GrowthOps OS built it, portal 247549241).

## 2. Bind the tenant and give its app a provision key

```bash
python -m crm_platform bind meridian --account meridian-supply           # pin the tenant; record its function domain
python -m crm_platform provision-key meridian --account meridian-supply  # PROVISION_KEY app secret + .secrets/ file
```

A personal access key cannot write standard CRM records or their schemas, so the loader's writes go through the
app's `provision` function, under the app's own token. That needs the app installed (step 3) before step 4.

`bind` refuses anything but a developer test account or sandbox, and every later run refuses a portal other than
the bound one. `apply` writes `evidence/<tenant>/apply.json`: operations, counts and IDs, never record values.

## 3. The app (before loading: the loader writes through it)

```bash
cd hubspot && npm run build                       # regenerate projects/ from the shared cards and functions
cd projects/meridian
npx hs project upload --profile live              # src/hsprofile.live.json names the account
npx hs project install-app --profile live
npm run set-secret -- HUBSPOT_CLIENT_SECRET <portal id>   # only for apps with signed endpoints; reads the clipboard
```

`bind` sets `ENDPOINT_BASE_URL` itself (a public URL, not a credential). HubSpot shows an app's client secret only
after its first successful deploy, so the first deploy leaves `HUBSPOT_CLIENT_SECRET` out of the functions' secrets
and the signed endpoints refuse every call until it is added; then rebind, rebuild and upload again. `set-secret`
reads the value from the clipboard because a paste into the CLI's hidden prompt is easy to lose (a blank secret
fails closed: every call answers 401).

## 4. Schema and records

```bash
python -m crm_platform plan meridian      # read-only: what would change (through the app, once a key exists)
python -m crm_platform apply meridian     # converge schema, load records, verify
python -m crm_platform verify meridian    # read-only: fails unless nothing is left to do
```

Cards appear after they are added to a record view: open a record, **Customize**, add the card from the app's card
library, save.

## 5. Workflows that use the custom actions

* **Meridian, "Pricing review" guardrail:** trigger on deal stage = Pricing review, action *Check the price
  guardrail*, then branch on *Needs approval* and create a task for the approver it names.
* **ScaleLab, "Paid means customer":** trigger on GrowthOps' closed-won flag, action *Advance lifecycle stage (never
  backwards)* with target *Customer*.

## 6. Watching it run

```bash
# HubSpot: Development > Monitoring > Logs > Endpoint functions / Webhooks / API calls (per app)
python -m crm_platform verify <tenant> --account <account>
```

A webhook delivery logs one line: `{"webhook":"accepted","events":2,"byType":{...},"eventIds":[...]}`. A refused
one logs the reason (`signature mismatch`, `stale timestamp`) and answers 401, which HubSpot retries.

## Rotating a key

Create the new personal access key in HubSpot, run `npx hs account auth` again, run `verify` for each tenant, then
revoke the old key. For the app's client secret: rotate it in the app's Auth settings, then
`npx hs secret update HUBSPOT_CLIENT_SECRET` in each project that has endpoint functions.
