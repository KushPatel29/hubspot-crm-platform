"""Copy the CRM-shaped slices of four portfolio projects into ``data/snapshots``.

Each business in this repo is a real project elsewhere in the portfolio. This script reads those projects from
their sibling checkouts and writes the rows a CRM needs, nothing else, so the HubSpot build here is reproducible in CI
without the other repositories. ``data/snapshots/manifest.json`` records, for every file written, the source
repository, its commit, the source file's SHA-256 and the rule that selected the rows. A test re-checks the row
counts and hashes of the committed snapshots against that manifest.

Run from the repo root with the source projects checked out next to it::

    python scripts/extract_sources.py            # rewrite the snapshots
    python scripts/extract_sources.py --check    # fail if a rewrite would change anything

Nothing here invents data except the buyer contact names (see :func:`buyer_name`): the source projects model
customer accounts, not the people in them, and a CRM company without a contact is not how anyone sells.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT.parent
OUT = ROOT / "data" / "snapshots"

REPOS = {
    "meridian": "cost-to-price-calculator",
    "crosssell": "Customer-Recommendation-Engine",
    "aml": "aml-transaction-monitoring",
}
PUBLIC_NAMES = {
    "cost-to-price-calculator": "KushPatel29/pricing-costing-analytics",
    "Customer-Recommendation-Engine": "KushPatel29/Customer-Recommendation-Engine",
    "aml-transaction-monitoring": "KushPatel29/aml-transaction-monitoring",
}
QUOTE_WINDOW_START = "2026-04-01"  # Meridian: the last full quarter of quotes becomes the deal book
RECOMMENDATIONS_PER_CUSTOMER = 3  # cross-sell: the top three eligible offers per account
HOT_PRIORITIES = ("P0", "P1")  # AML: counterparties are loaded for the cases a person must look at first
SHARED_MINIMUM = 2  # AML: a counterparty is loaded when at least this many hot subjects transact with it

FIRST_NAMES = ("Avery", "Jordan", "Priya", "Mateo", "Hannah", "Wei", "Noor", "Liam", "Sofia", "Daniel", "Amara",
               "Ethan", "Chloe", "Ravi", "Maya", "Lucas", "Aisha", "Owen", "Leah", "Kenji", "Elena", "Samuel",
               "Zara", "Marcus")
LAST_NAMES = ("Tremblay", "Nguyen", "Patel", "Martin", "Singh", "Roy", "Chen", "Gagnon", "Wilson", "Kaur", "Brown",
              "Lee", "Côté", "Taylor", "Ahmed", "Campbell", "Wong", "Morin", "Anderson", "Garcia", "Dubois",
              "Clarke", "Kim", "Fraser")


def buyer_name(key: str) -> tuple[str, str]:
    """A deterministic synthetic buyer name for an account. Labelled synthetic wherever it is loaded."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return FIRST_NAMES[digest[0] % len(FIRST_NAMES)], LAST_NAMES[digest[1] % len(LAST_NAMES)]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def commit(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True,
                          text=True).stdout.strip()


def read(path: Path) -> list[dict[str, str]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def render(rows: list[dict[str, str]], fields: Iterable[str]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(fields), lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _money(value: float) -> str:
    return f"{value:.4f}"


def meridian(repo: Path) -> dict[str, tuple[str, list[str], str]]:
    data = repo / "data"
    customers = read(data / "dim_customer.csv")
    products = read(data / "dim_product.csv")
    people = read(data / "dim_salesperson.csv")
    quotes = read(data / "fact_quote.csv")

    by_product: dict[str, list[dict[str, str]]] = defaultdict(list)
    for quote in quotes:
        by_product[quote["product_id"]].append(quote)
    for product in products:
        seen = by_product.get(product["product_id"], [])
        cost = (float(product["base_input_cost_unit"]) + float(product["handling_cost_unit"])
                + float(product["labelling_cost_unit"]))
        if seen:
            product["reference_list_price"] = _money(statistics.median(float(q["list_price"]) for q in seen))
            product["reference_unit_cost"] = _money(statistics.median(float(q["final_cost"]) for q in seen))
        else:
            product["reference_unit_cost"] = _money(cost)
            product["reference_list_price"] = _money(cost / (1 - float(product["target_margin"])))
    for customer in customers:
        customer["buyer_firstname"], customer["buyer_lastname"] = buyer_name(customer["customer_id"])
    window = [q for q in quotes if q["month"] >= QUOTE_WINDOW_START]
    window.sort(key=lambda q: (q["month"], q["customer_id"], q["quote_id"]))

    source = "data/"
    return {
        "customers.csv": (render(customers, [*customers[0].keys()]), [f"{source}dim_customer.csv"],
                          "every customer; buyer_firstname/buyer_lastname are synthetic (buyer_name)"),
        "products.csv": (render(products, [*products[0].keys()]),
                         [f"{source}dim_product.csv", f"{source}fact_quote.csv"],
                         ("every product; reference_list_price and reference_unit_cost are the medians of that "
                         "product's quoted list price and final cost over all quotes, or cost / (1 - target "
                         "margin) for a product never quoted")),
        "salespeople.csv": (render(people, [*people[0].keys()]), [f"{source}dim_salesperson.csv"], "every salesperson"),
        "quotes.csv": (render(window, [*quotes[0].keys()]), [f"{source}fact_quote.csv"],
                       f"quotes with month >= {QUOTE_WINDOW_START}, sorted by month, customer, quote"),
    }


def crosssell(repo: Path) -> dict[str, tuple[str, list[str], str]]:
    customers = read(repo / "data" / "customers.csv")
    analytics = {row["customer_id"]: row for row in read(repo / "output" / "customer_analytics.csv")}
    catalog = read(repo / "data" / "catalog.csv")
    recommendations = read(repo / "output" / "cross_sell_recommendations.csv")
    audit = {(r["customer_id"], r["rank"], r["sku"]): r
             for r in read(repo / "output" / "recommendation_eligibility_audit.csv")}

    keep = ("total_revenue", "total_margin", "orders", "distinct_skus", "last_order", "recency_days",
            "median_reorder_days", "days_overdue", "churn_risk", "rfm_segment", "margin_pct", "clv_12m_runrate")
    for customer in customers:
        row = analytics[customer["customer_id"]]
        customer.update({k: row[k] for k in keep})
        customer["buyer_firstname"], customer["buyer_lastname"] = buyer_name(customer["customer_id"])

    chosen: list[dict[str, str]] = []
    taken: Counter[str] = Counter()
    for rec in sorted(recommendations, key=lambda r: (r["customer_id"], int(r["rank"]))):
        verdict = audit[(rec["customer_id"], rec["rank"], rec["sku"])]
        eligible = verdict["eligible"].strip().lower() in ("true", "1")
        if not eligible or taken[rec["customer_id"]] >= RECOMMENDATIONS_PER_CUSTOMER:
            continue
        taken[rec["customer_id"]] += 1
        chosen.append({**rec, "eligibility_decision": verdict["eligibility_decision"]})

    return {
        "customers.csv": (render(customers, [*customers[0].keys()]),
                          ["data/customers.csv", "output/customer_analytics.csv"],
                          f"every customer joined to its analytics ({', '.join(keep)}); buyer names synthetic"),
        "catalog.csv": (render(catalog, [*catalog[0].keys()]), ["data/catalog.csv"], "every SKU"),
        "recommendations.csv": (render(chosen, [*recommendations[0].keys(), "eligibility_decision"]),
                                ["output/cross_sell_recommendations.csv",
                                 "output/recommendation_eligibility_audit.csv"],
                                (f"the top {RECOMMENDATIONS_PER_CUSTOMER} recommendations per customer that pass the "
                                "eligibility audit, in rank order")),
    }


def aml(repo: Path) -> dict[str, tuple[str, list[str], str]]:
    cases = read(repo / "results" / "investigation_case_register.csv")
    entities = {row["entity_id"]: row for row in read(repo / "data" / "generated" / "entities.csv.gz")}
    counterparties = {row["counterparty_id"]: row
                      for row in read(repo / "data" / "generated" / "counterparties.csv.gz")}
    graph = read(repo / "results" / "investigation_entity_graph.csv")
    typologies = read(repo / "results" / "typology_evidence_catalogue.csv")

    subjects = [entities[case["entity_id"]] for case in cases]
    hot = {case["entity_id"] for case in cases if case["priority"] in HOT_PRIORITIES}
    hot_edges = [edge for edge in graph if edge["entity_id"] in hot]
    degree = Counter(edge["counterparty_id"] for edge in hot_edges)
    shared = sorted(cp for cp, n in degree.items() if n >= SHARED_MINIMUM)
    links = sorted((e for e in hot_edges if e["counterparty_id"] in set(shared)),
                   key=lambda e: (e["entity_id"], e["counterparty_id"]))
    loaded = [{**counterparties[cp], "hot_subjects": str(degree[cp])} for cp in shared]

    return {
        "cases.csv": (render(cases, [*cases[0].keys()]), ["results/investigation_case_register.csv"], "every case"),
        "subjects.csv": (render(subjects, [*subjects[0].keys()]), ["data/generated/entities.csv.gz"],
                         "the entity each case is about, in case order"),
        "counterparties.csv": (render(loaded, [*next(iter(counterparties.values())).keys(), "hot_subjects"]),
                               ["data/generated/counterparties.csv.gz", "results/investigation_entity_graph.csv"],
                               (f"counterparties that at least {SHARED_MINIMUM} subjects of {'/'.join(HOT_PRIORITIES)} "
                               "cases transact with; hot_subjects is that count")),
        "links.csv": (render(links, [*graph[0].keys()]), ["results/investigation_entity_graph.csv"],
                      "entity-graph edges between those subjects and those counterparties"),
        "typologies.csv": (render(typologies, [*typologies[0].keys()]), ["results/typology_evidence_catalogue.csv"],
                           "the whole typology evidence catalogue"),
    }


BUILDERS = {"meridian": meridian, "crosssell": crosssell, "aml": aml}


def build() -> tuple[dict[Path, str], dict]:
    files: dict[Path, str] = {}
    manifest: dict = {"generated_by": "scripts/extract_sources.py", "tenants": {}}
    for tenant, folder in REPOS.items():
        repo = SOURCES / folder
        if not repo.exists():
            raise SystemExit(f"source project not found: {repo}")
        entries = []
        for name, (text, sources, rule) in BUILDERS[tenant](repo).items():
            path = OUT / tenant / name
            files[path] = text
            entries.append({
                "file": f"data/snapshots/{tenant}/{name}",
                "rows": text.count("\n") - 1,
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "sources": [{"path": s, "sha256": sha256(repo / s)} for s in sources],
                "rule": rule,
            })
        manifest["tenants"][tenant] = {"repository": PUBLIC_NAMES[folder], "commit": commit(repo), "files": entries}
    return files, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the committed snapshots differ from a rebuild")
    args = parser.parse_args()
    files, manifest = build()
    files[OUT / "manifest.json"] = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    stale = [p for p, text in files.items() if not p.exists() or p.read_text(encoding="utf-8") != text]
    if args.check:
        if stale:
            sys.exit("stale snapshots: " + ", ".join(str(p.relative_to(ROOT)) for p in stale))
        print("snapshots match their sources")
        return
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    for tenant, info in manifest["tenants"].items():
        print(tenant, info["commit"][:7], ", ".join(f"{Path(f['file']).name} {f['rows']}" for f in info["files"]))


if __name__ == "__main__":
    main()
