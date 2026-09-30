"""Meridian Supply: a multi-category B2B wholesale distributor's quote book, with its pricing guardrail.

Source: KushPatel29/pricing-costing-analytics (``data/snapshots/meridian``). What lands in HubSpot:

* 150 **companies** (customers) with their segment, channel, tier, price list, terms and salesperson, each with one
  synthetic buyer **contact** associated as "Buyer" and as the contact's primary company.
* 240 **products** at the median quoted list price and cost, each carrying its target margin and the guardrail band
  the pricing project derives from it.
* One **deal** per customer, month and outcome for April to June 2026 (a request for quotes that was partly won is
  split into its won and its lost lines, the way a rep closes a partial win), in a "Quote to order" pipeline, with
  one **line item** per quote line tied to its product.
* The guardrail verdict, approver and margin gap on every deal and line, computed by :mod:`crm_platform.guardrails`
  from the prices and costs exactly as HubSpot stores them (two decimals), so HubSpot's own recalculation agrees.

The salesperson is a property, not the HubSpot owner: creating twelve users in a test portal would send twelve
invitations. Buyer names are synthetic and labelled so.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date

from crm_platform import guardrails
from crm_platform.model import (
    PRIMARY,
    AssociationLabel,
    Link,
    ObjectModel,
    Pipeline,
    Property,
    Record,
    Stage,
    TenantModel,
    enum_of,
    key_property,
    options,
    pipeline_ref,
    record_ref,
    slug,
    stage_ref,
)
from crm_platform.snapshots import ascii_slug, money, month_end, number, rows

KEY = "meridian"
NAME = "Meridian Supply"
GROUP = "meridian_pricing"
PIPELINE = "Quote to order"
STAGES = (Stage("Draft", probability=0.1), Stage("Pricing review", probability=0.3),
          Stage("Sent to customer", probability=0.6), Stage("Won", closed=True, probability=1.0),
          Stage("Lost", closed=True, probability=0.0))
VERDICT_OPTIONS = enum_of(list(guardrails.VERDICTS))
APPROVER_OPTIONS = enum_of(list(guardrails.APPROVERS))


def _distinct(table: list[dict[str, str]], column: str) -> list[str]:
    return sorted({r[column] for r in table if r[column]})


def model() -> TenantModel:
    customers = rows(KEY, "customers.csv")
    products = rows(KEY, "products.csv")
    quotes = rows(KEY, "quotes.csv")

    def enum(name: str, label: str, values: list[str], description: str = "") -> Property:
        return Property(name, label, "enumeration", "select", description, enum_of(values))

    def num(name: str, label: str, description: str = "") -> Property:
        return Property(name, label, "number", "number", description)

    companies = ObjectModel("companies", GROUP, "Meridian pricing", (
        key_property("companies"),
        enum("meridian_segment", "Segment", _distinct(customers, "segment")),
        enum("meridian_channel", "Channel", _distinct(customers, "channel")),
        enum("meridian_region", "Sales region", _distinct(customers, "region")),
        enum("meridian_tier", "Account tier", _distinct(customers, "tier")),
        enum("meridian_price_list", "Price list", _distinct(customers, "price_list")),
        enum("meridian_payment_terms", "Payment terms", _distinct(customers, "payment_terms")),
        num("meridian_price_sensitivity", "Price sensitivity",
            "The pricing project's elasticity score for the account."),
        enum("meridian_salesperson", "Salesperson", _distinct(customers, "salesperson"),
             "Kept as a property: the test portal does not create a HubSpot user per salesperson."),
    ))
    contacts = ObjectModel("contacts", GROUP, "Meridian pricing", (key_property("contacts"),))
    catalog = ObjectModel("products", GROUP, "Meridian pricing", (
        key_property("products"),
        enum("meridian_category", "Category", _distinct(products, "category")),
        Property("meridian_sub_category", "Sub-category", "string", "text"),
        enum("meridian_brand_tier", "Brand tier", _distinct(products, "brand_tier")),
        enum("meridian_lifecycle", "Lifecycle", _distinct(products, "lifecycle")),
        Property("meridian_supplier", "Supplier", "string", "text"),
        num("meridian_target_margin", "Target margin", "Fraction of price. The guardrail band is derived from it."),
        num("meridian_floor_margin", "Floor margin", "max(5%, target - 9 points): under it a deal needs a signature."),
        num("meridian_stretch_margin", "Stretch margin", "target + 8 points."),
    ))
    deals = ObjectModel("deals", GROUP, "Meridian pricing", (
        key_property("deals"),
        Property("meridian_quote_month", "Quote month", "date", "date"),
        Property("meridian_guardrail_verdict", "Guardrail verdict", "enumeration", "select",
                 "The worst line's position in its margin band (invoice margin).", VERDICT_OPTIONS),
        Property("meridian_approver", "Approver needed", "enumeration", "select",
                 "The most senior signature any line needs, tiered on the gap to target.", APPROVER_OPTIONS),
        num("meridian_blended_margin", "Blended margin", "Invoice margin across every line, as a fraction of price."),
        num("meridian_margin_gap_dollars", "Margin under target ($)", "What the approver is asked to give up."),
        enum("meridian_loss_reason", "Loss reason", _distinct(quotes, "loss_reason"),
             "The most common reason across the deal's lost lines."),
    ))
    line_items = ObjectModel("line_items", GROUP, "Meridian pricing", (
        key_property("line_items"),
        num("meridian_list_price", "List price"),
        num("meridian_competitor_price", "Competitor price"),
        Property("meridian_line_verdict", "Guardrail verdict", "enumeration", "select", "", VERDICT_OPTIONS),
        Property("meridian_line_outcome", "Line outcome", "enumeration", "select", "",
                 options(("won", "Won"), ("lost", "Lost"))),
    ))
    return TenantModel(
        KEY, NAME,
        "A multi-category B2B wholesale distributor: accounts, catalogue and quote book with a margin guardrail.",
        objects=(companies, contacts, catalog, deals, line_items),
        pipelines=(Pipeline("deals", PIPELINE, STAGES),),
        associations=(AssociationLabel("contacts", "companies", "buyer", "Buyer", "Buyer"),),
    )


def domain(customer: dict[str, str]) -> str:
    return f"{ascii_slug(customer['customer_name'])}-{customer['customer_id'].lower()}.example.com"


def records() -> list[Record]:
    customers = rows(KEY, "customers.csv")
    products = {p["product_id"]: p for p in rows(KEY, "products.csv")}
    quotes = rows(KEY, "quotes.csv")
    out: list[Record] = []

    for c in customers:
        out.append(Record("companies", c["customer_id"], {
            "name": c["customer_name"], "domain": domain(c),
            "meridian_segment": slug(c["segment"]), "meridian_channel": slug(c["channel"]),
            "meridian_region": slug(c["region"]), "meridian_tier": slug(c["tier"]),
            "meridian_price_list": slug(c["price_list"]), "meridian_payment_terms": slug(c["payment_terms"]),
            "meridian_price_sensitivity": number(c["price_sensitivity"]),
            "meridian_salesperson": slug(c["salesperson"]),
        }))
        first, last = c["buyer_firstname"], c["buyer_lastname"]
        out.append(Record("contacts", f"{c['customer_id']}-buyer", {
            "firstname": first, "lastname": last, "jobtitle": "Purchasing lead (synthetic contact)",
            "email": f"{ascii_slug(first, '.')}.{ascii_slug(last, '.')}@{domain(c)}",
        }, links=(Link("companies", c["customer_id"], "buyer"), Link("companies", c["customer_id"], PRIMARY))))

    for p in products.values():
        floor, target, stretch = guardrails.band(float(p["target_margin"]))
        out.append(Record("products", p["product_id"], {
            "name": p["description"], "hs_sku": p["product_id"], "price": money(p["reference_list_price"]),
            "hs_cost_of_goods_sold": money(p["reference_unit_cost"]),
            "description": f"{p['category']} · {p['sub_category']} · {p['pack_format']}",
            "meridian_category": slug(p["category"]), "meridian_sub_category": p["sub_category"],
            "meridian_brand_tier": slug(p["brand_tier"]), "meridian_lifecycle": slug(p["lifecycle"]),
            "meridian_supplier": p["supplier"], "meridian_target_margin": number(target),
            "meridian_floor_margin": number(floor), "meridian_stretch_margin": number(stretch),
        }))

    names = {c["customer_id"]: c["customer_name"] for c in customers}
    groups: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for q in quotes:
        groups[(q["customer_id"], q["month"], q["outcome"].lower())].append(q)
    split = Counter((cust, month) for cust, month, _ in groups)
    for (customer, month, outcome), lines in sorted(groups.items()):
        key = f"RFQ-{customer}-{month[:7]}-{outcome}"
        priced = [(float(money(q["quoted_price"])), float(money(q["final_cost"])), float(q["quantity_units"]),
                   float(products[q["product_id"]]["target_margin"])) for q in lines]
        score = guardrails.score_deal(priced)
        label = date.fromisoformat(month).strftime("%B %Y")
        suffix = f" ({outcome} lines)" if split[(customer, month)] > 1 else ""
        reasons = Counter(q["loss_reason"] for q in lines if q["loss_reason"])
        props = {
            "dealname": f"{names[customer]} · {label} RFQ{suffix}",
            "amount": money(sum(price * qty for price, _, qty, _ in priced)),
            "pipeline": pipeline_ref("deals", PIPELINE),
            "dealstage": stage_ref("deals", PIPELINE, "Won" if outcome == "won" else "Lost"),
            "closedate": month_end(month), "meridian_quote_month": month,
            "meridian_guardrail_verdict": slug(score.verdict), "meridian_approver": slug(score.approver),
            "meridian_blended_margin": number(score.blended_margin_pct),
            "meridian_margin_gap_dollars": money(score.gap_dollars),
        }
        if reasons:
            props["meridian_loss_reason"] = slug(sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))[0][0])
        out.append(Record("deals", key, props, links=(
            Link("companies", customer), Link("contacts", f"{customer}-buyer"))))
        for q, (price, cost, qty, _), line in zip(lines, priced, score.lines, strict=True):
            out.append(Record("line_items", q["quote_id"], {
                "name": products[q["product_id"]]["description"], "quantity": number(qty),
                "price": money(price), "hs_cost_of_goods_sold": money(cost),
                "hs_product_id": record_ref("products", q["product_id"]),
                "meridian_list_price": money(q["list_price"]),
                "meridian_competitor_price": money(q["competitor_price"]),
                "meridian_line_verdict": slug(line.verdict), "meridian_line_outcome": outcome,
            }, links=(Link("deals", key),)))
    return out
