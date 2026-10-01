"""A specialty meat distributor's accounts and the cross-sell engine's next-best offers, as a CRM custom object.

Source: KushPatel29/Customer-Recommendation-Engine (``data/snapshots/crosssell``). What lands in HubSpot:

* 120 **companies** (restaurants, grocers, delis) with the engine's account analytics: RFM segment, churn risk,
  12-month value, revenue, margin, recency and how overdue the next order is. Each has one synthetic buyer
  **contact**, associated as "Buyer" and as the contact's primary company.
* 38 **products**, one per SKU, at the catalogue price and cost.
* A custom object, **Recommendation**, for the offers that passed the engine's eligibility audit, at most three per
  account (302 in all). Each carries its rank, score, estimated revenue, and the similar account that explains it,
  and is associated with its company as "Recommended for".

Reps work the offers from the "Next best offer" card on the company record: accepting one creates a deal with the
product as a line item and associates it with the offer as "Converted to deal". ``offer_status`` is set only when an
offer is created, so rerunning the load never reopens an offer a rep accepted or dismissed.

**Deals** get only the platform key here, and no records: the card's function writes it on the deal an offer
becomes (``offer-deal:<offer id>``). It is unique, so HubSpot itself refuses a second deal for the same offer when
two reps, or a double click, accept it at once.
"""

from __future__ import annotations

from crm_platform.model import (
    PRIMARY,
    AssociationLabel,
    Link,
    ObjectModel,
    Property,
    Record,
    TenantModel,
    enum_of,
    key_property,
    options,
    slug,
)
from crm_platform.snapshots import ascii_slug, money, number, rows

KEY = "crosssell"
NAME = "Specialty meat distributor"
GROUP = "cross_sell"
OFFER_STATUSES = options(("open", "Open"), ("accepted", "Accepted"), ("dismissed", "Dismissed"))


def _distinct(table: list[dict[str, str]], column: str, order: tuple[str, ...] = ()) -> list[str]:
    values = {r[column] for r in table if r[column]}
    return [v for v in order if v in values] + sorted(values - set(order))


def model() -> TenantModel:
    customers = rows(KEY, "customers.csv")
    catalog = rows(KEY, "catalog.csv")

    def enum(name: str, label: str, values: list[str], description: str = "") -> Property:
        return Property(name, label, "enumeration", "select", description, enum_of(values))

    def num(name: str, label: str, description: str = "") -> Property:
        return Property(name, label, "number", "number", description)

    companies = ObjectModel("companies", GROUP, "Cross-sell analytics", (
        key_property("companies"),
        enum("xsell_region", "Region", _distinct(customers, "region")),
        enum("xsell_persona", "Account type", _distinct(customers, "persona")),
        enum("xsell_rep", "Rep", _distinct(customers, "rep"), "Kept as a property: no HubSpot user per rep."),
        enum("xsell_rfm_segment", "RFM segment", _distinct(customers, "rfm_segment")),
        enum("xsell_churn_risk", "Churn risk", _distinct(customers, "churn_risk", ("Low", "Medium", "High"))),
        num("xsell_clv_12m", "12-month value (run rate)"),
        num("xsell_total_revenue", "Revenue to date"),
        num("xsell_margin_pct", "Margin", "Invoice margin as a fraction of revenue."),
        num("xsell_orders", "Orders"),
        Property("xsell_last_order", "Last order", "date", "date"),
        num("xsell_recency_days", "Days since last order"),
        num("xsell_median_reorder_days", "Usual days between orders"),
        num("xsell_days_overdue", "Days overdue", "Days past the usual reorder interval."),
    ))
    contacts = ObjectModel("contacts", GROUP, "Cross-sell analytics", (key_property("contacts"),))
    deals = ObjectModel("deals", GROUP, "Cross-sell analytics", (key_property("deals"),))
    products = ObjectModel("products", GROUP, "Cross-sell analytics", (
        key_property("products"), enum("xsell_protein", "Protein", _distinct(catalog, "protein")),
    ))
    recommendation = ObjectModel(
        "recommendation", "recommendation_details", "Recommendation details", (
            key_property("recommendation", custom=True),
            Property("offer_title", "Offer", "string", "text"),
            num("offer_rank", "Rank"),
            num("offer_score", "Score", "Item-neighbourhood collaborative-filtering score."),
            Property("offer_sku", "SKU", "string", "text"),
            enum("offer_protein", "Protein", _distinct(catalog, "protein")),
            num("offer_revenue_opportunity", "Estimated revenue opportunity"),
            Property("offer_because", "Because similar to", "string", "text",
                     "The account whose purchases put this product at the top."),
            Property("offer_eligibility", "Eligibility decision", "string", "text"),
            Property("offer_status", "Status", "enumeration", "select", "", OFFER_STATUSES),
            Property("offer_decided_at", "Decided at", "datetime", "date"),
            Property("offer_decision_note", "Decision note", "string", "textarea"),
            Property("offer_model_version", "Model", "string", "text"),
        ),
        custom=True, singular="Recommendation", plural="Recommendations", primary_display="offer_title",
        secondary_display=("offer_status", "offer_revenue_opportunity"), searchable=("offer_title", "offer_sku"),
        required=("offer_title",), associated_objects=("companies", "deals"),
    )
    return TenantModel(
        KEY, NAME, "Account analytics and next-best offers from the cross-sell engine, worked from the company record.",
        objects=(companies, contacts, products, deals, recommendation),
        associations=(
            AssociationLabel("contacts", "companies", "buyer", "Buyer", "Buyer"),
            AssociationLabel("recommendation", "companies", "recommended_for", "Recommended for", "Recommendation"),
            AssociationLabel("recommendation", "deals", "converted_to", "Converted to deal", "Source recommendation"),
        ),
    )


def domain(customer: dict[str, str]) -> str:
    return f"{ascii_slug(customer['customer_name'])}.example.com"


def records() -> list[Record]:
    out: list[Record] = []
    for c in rows(KEY, "customers.csv"):
        out.append(Record("companies", c["customer_id"], {
            "name": c["customer_name"], "domain": domain(c),
            "xsell_region": slug(c["region"]), "xsell_persona": slug(c["persona"]), "xsell_rep": slug(c["rep"]),
            "xsell_rfm_segment": slug(c["rfm_segment"]), "xsell_churn_risk": slug(c["churn_risk"]),
            "xsell_clv_12m": money(c["clv_12m_runrate"]), "xsell_total_revenue": money(c["total_revenue"]),
            "xsell_margin_pct": number(c["margin_pct"]), "xsell_orders": number(c["orders"]),
            "xsell_last_order": c["last_order"], "xsell_recency_days": number(c["recency_days"]),
            "xsell_median_reorder_days": number(c["median_reorder_days"]),
            "xsell_days_overdue": number(c["days_overdue"]),
        }))
        first, last = c["buyer_firstname"], c["buyer_lastname"]
        out.append(Record("contacts", f"{c['customer_id']}-buyer", {
            "firstname": first, "lastname": last, "jobtitle": "Buyer (synthetic contact)",
            "email": f"{ascii_slug(first, '.')}.{ascii_slug(last, '.')}@{domain(c)}",
        }, links=(Link("companies", c["customer_id"], "buyer"), Link("companies", c["customer_id"], PRIMARY))))
    for item in rows(KEY, "catalog.csv"):
        out.append(Record("products", item["sku"], {
            "name": item["description"], "hs_sku": item["sku"], "price": money(item["unit_price"]),
            "hs_cost_of_goods_sold": money(item["unit_cost"]), "description": f"{item['protein']} · per lb",
            "xsell_protein": slug(item["protein"]),
        }))
    for rec in rows(KEY, "recommendations.csv"):
        out.append(Record("recommendation", f"{rec['customer_id']}:{rec['sku']}", {
            "offer_title": f"#{rec['rank']} {rec['description']} ({rec['protein']}) for {rec['customer_name']}",
            "offer_rank": rec["rank"], "offer_score": number(rec["score"]), "offer_sku": rec["sku"],
            "offer_protein": slug(rec["protein"]), "offer_revenue_opportunity": money(rec["est_revenue_opportunity"]),
            "offer_because": rec["because_similar_to"], "offer_eligibility": rec["eligibility_decision"],
            "offer_status": "open", "offer_model_version": "item-neighbourhood CF (shipped model)",
        }, links=(Link("companies", rec["customer_id"], "recommended_for"),),
            create_only=frozenset({"offer_status"})))
    return out
