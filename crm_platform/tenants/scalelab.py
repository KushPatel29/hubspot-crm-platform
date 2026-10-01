"""ScaleLab (GrowthOps OS): the portal GrowthOps already builds and syncs, extended by an app, not re-modelled.

KushPatel29/GrowthOps-OS owns this portal's data model and its two-way sync (a field contract, approved change
sets, signed webhooks). This repo must not fight it, so the tenant declares no objects and loads no records. It
declares what its app *reads*: the GrowthOps contact properties the "Revenue truth" card and the lifecycle workflow
action depend on, and the deal properties the card reads through the contact's associated deals (what was
bought). Preflight checks each one exists before the app is installed, and fails naming any that does not; it
first caught the card reading ``growthops_product`` from contacts, where GrowthOps does not write it.
"""

from __future__ import annotations

from crm_platform.model import TenantModel

KEY = "scalelab"
NAME = "ScaleLab (GrowthOps OS)"
READS = (
    "growthops_contact_id", "growthops_net_cash", "growthops_original_source", "growthops_first_touch_campaign",
    "growthops_lead_creation_campaign", "growthops_last_non_direct_campaign", "growthops_tracking_status",
    "growthops_mql_date", "growthops_call_booked_date", "growthops_has_closed_won", "growthops_renewal_due_date",
    "growthops_renewal_risk",
)
DEAL_READS = ("growthops_product", "growthops_net_cash")


def model() -> TenantModel:
    return TenantModel(
        KEY, NAME, "GrowthOps' portal: an app card and a workflow action over the data GrowthOps already syncs.",
        requires=tuple(("contacts", name) for name in READS) + (("contacts", "lifecyclestage"),)
        + tuple(("deals", name) for name in DEAL_READS),
    )


def records() -> list:
    return []
