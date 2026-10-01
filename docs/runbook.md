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
endpoints need Enterprise). Test accounts are created from the parent account's CLI login, and the keys the CLI
makes for them renew through the parent's key, so the parent's key must stay active.

```bash
npx hs account auth --account <parent id> --name dev     # inside the repo: outside it, npx fetches an unrelated "hs"
npx hs test-account create -a dev --name "Meridian Supply" --sales-level ENTERPRISE --service-level ENTERPRISE \
  --marketing-level ENTERPRISE --content-level ENTERPRISE --ops-level ENTERPRISE --commerce-level ENTERPRISE
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

## Verified identity on card actions

Once an app's client secret is in its portal (`npm run set-secret -- HUBSPOT_CLIENT_SECRET <portal id>`), rebind,
rebuild and upload: the actions that record who acted move to signed endpoints and the cards call them with
`hubspot.fetch`.

```bash
python -m crm_platform bind aml --account aml-investigations   # records that the secret now exists
cd hubspot && npm run build
cd projects/aml && npx hs project upload --profile live
npx hs project deploy --profile live --force --build <n>       # the private function is removed: HubSpot asks
```

A deploy that removes a component is refused with a warning until it is forced; `--force` needs `--build`.

## The nightly live check

`.github/workflows/live-verify.yml` plans every portal read-only each night. Give it each portal's provision key as a
repository secret (GitHub > Settings > Secrets and variables > Actions): `PROVISION_KEY_MERIDIAN`,
`PROVISION_KEY_CROSSSELL`, `PROVISION_KEY_AML`. The values are the files `provision-key` wrote:

```powershell
Get-Content .secrets\meridian.provision.key | Set-Clipboard    # then paste into the secret's value box
```

## Salesforce

A free Developer Edition org (developer.salesforce.com/signup), then:

```bash
npx sf org login web --alias crm-dev                           # log in in the browser it opens
cd salesforce/meridian
npx sf project deploy start --source-dir force-app --source-dir code --test-level RunLocalTests --target-org crm-dev
npx sf org assign permset --name Crm_Platform_Meridian --name Meridian_Guardrail_Code --target-org crm-dev
cd ../.. && python -m crm_platform.salesforce.load meridian --org crm-dev            # plan
python -m crm_platform.salesforce.load meridian --org crm-dev --write               # load, then plan again: zero
```

The deploy runs the 11 Apex tests, including the 359-deal parity test. The loader refuses anything but a Developer
Edition org or a sandbox and binds the tenant to the first org it loads. Add the *Deal margin guardrail* component
to the Opportunity record page in Lightning App Builder. For CI, store the org's auth URL
(`npx sf org display --verbose --json --target-org crm-dev`, the `sfdxAuthUrl` value) as the `SFDX_AUTH_URL` secret.

## Rotating a key

Create the new personal access key in HubSpot, run `npx hs account auth` again, run `verify` for each tenant, then
revoke the old key. For the app's client secret: rotate it in the app's Auth settings, then
`npx hs secret update HUBSPOT_CLIENT_SECRET` in each project that has endpoint functions.
