"""The four businesses this platform runs a CRM for, each a project elsewhere in the portfolio."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType

from crm_platform.model import Record, TenantModel
from crm_platform.tenants import aml, crosssell, meridian, scalelab


@dataclass(frozen=True)
class Tenant:
    key: str
    name: str
    model: Callable[[], TenantModel]
    records: Callable[[], list[Record]]


def _tenant(module: ModuleType) -> Tenant:
    return Tenant(module.KEY, module.NAME, module.model, module.records)


TENANTS: dict[str, Tenant] = {t.key: t for t in map(_tenant, (scalelab, meridian, crosssell, aml))}


def get(key: str) -> Tenant:
    try:
        return TENANTS[key]
    except KeyError:
        raise SystemExit(f"unknown tenant {key!r}; one of {', '.join(TENANTS)}") from None
