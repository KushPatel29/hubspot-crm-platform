"""What a business needs in its CRM, written once and compiled to HubSpot (and to Salesforce metadata).

A :class:`TenantModel` is the declared state of one business's CRM: which objects exist (standard ones and custom
ones), the properties this platform manages on each, the pipelines, and the association labels between objects. It
says nothing about HubSpot's or Salesforce's APIs; :mod:`crm_platform.hubspot.compile` and
:mod:`crm_platform.salesforce.metadata` translate it.

Two rules make one model safe to compile to both CRMs:

* **Names fit both.** A name is lower snake case, starts with a letter, has no double underscore, does not start
  with ``hs_`` (HubSpot's reserved prefix) and is at most 40 characters (Salesforce's API-name limit, before
  ``__c``). :meth:`TenantModel.problems` enforces every rule, and a test runs it over every tenant.
* **Records are addressed by a key the business owns.** Every object carries :data:`KEY_PROPERTY`, the identifier
  from the source system. The platform never matches records by CRM-generated IDs or by display names, so a
  rerun updates what it created before instead of duplicating it, and the same records could be loaded into
  Salesforce by external ID.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

KEY_PROPERTY = "crm_platform_key"
STANDARD_OBJECTS = ("contacts", "companies", "deals", "products", "line_items", "tickets")
# HubSpot lets a unique-value property exist on these; products and line items are matched by reading them back.
UNIQUE_CAPABLE = frozenset({"contacts", "companies", "deals", "tickets"})
PIPELINE_OBJECTS = frozenset({"deals", "tickets"})
FIELD_TYPES: Mapping[str, frozenset[str]] = {
    "string": frozenset({"text", "textarea", "phonenumber", "html"}),
    "number": frozenset({"number"}),
    "date": frozenset({"date"}),
    "datetime": frozenset({"date"}),
    "enumeration": frozenset({"select", "radio", "checkbox"}),
    "bool": frozenset({"booleancheckbox"}),
}
NAME = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
MAX_NAME = 40
MAX_UNIQUE_PER_OBJECT = 10


@dataclass(frozen=True)
class Option:
    value: str
    label: str


@dataclass(frozen=True)
class Property:
    name: str
    label: str
    type: str
    field_type: str
    description: str = ""
    options: tuple[Option, ...] = ()
    unique: bool = False


@dataclass(frozen=True)
class ObjectModel:
    """One CRM object and the properties this platform manages on it.

    For a standard object only the listed properties are managed; HubSpot's own (``email``, ``dealname``...) are
    written by the records but never created. A custom object (``custom=True``) is created from this definition.
    """

    name: str
    group: str
    group_label: str
    properties: tuple[Property, ...]
    custom: bool = False
    singular: str = ""
    plural: str = ""
    primary_display: str = ""
    secondary_display: tuple[str, ...] = ()
    searchable: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    associated_objects: tuple[str, ...] = ()
    has_pipeline: bool = False

    def prop(self, name: str) -> Property:
        for prop in self.properties:
            if prop.name == name:
                return prop
        raise KeyError(f"{self.name}.{name}")

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.properties)


@dataclass(frozen=True)
class Stage:
    label: str
    closed: bool = False
    probability: float | None = None  # deals only: HubSpot's win probability for the stage


@dataclass(frozen=True)
class Pipeline:
    object_name: str
    label: str
    stages: tuple[Stage, ...]


@dataclass(frozen=True)
class AssociationLabel:
    """A named relationship between two objects (``case`` → ``contact`` as "Subject")."""

    from_object: str
    to_object: str
    name: str
    label: str
    inverse_label: str = ""


@dataclass(frozen=True)
class Link:
    """One association a record should have: to the record with ``to_key``, with a label (``""`` = unlabelled)."""

    to_object: str
    to_key: str
    label: str = ""


@dataclass(frozen=True)
class Record:
    """One record as the source system says it should be.

    ``create_only`` names properties set when the record is created and never again: values people change in the
    CRM afterwards (a pipeline stage an investigator moved, an offer a rep accepted) are theirs, and a rerun must not
    put them back. A property value can be a reference resolved against the portal when it is written:
    :func:`pipeline_ref`, :func:`stage_ref`, :func:`record_ref` or :data:`NOW`.
    """

    object_name: str
    key: str
    properties: Mapping[str, str]
    links: tuple[Link, ...] = ()
    create_only: frozenset[str] = frozenset()


NOW = "@now"
PRIMARY = "primary"  # the CRM's own "primary" association label (a contact's primary company)


def pipeline_ref(object_name: str, label: str) -> str:
    return f"@pipeline:{object_name}:{label}"


def stage_ref(object_name: str, pipeline: str, stage: str) -> str:
    return f"@stage:{object_name}:{pipeline}:{stage}"


def record_ref(object_name: str, key: str) -> str:
    return f"@record:{object_name}:{key}"


@dataclass(frozen=True)
class TenantModel:
    key: str
    name: str
    description: str
    objects: tuple[ObjectModel, ...] = ()
    pipelines: tuple[Pipeline, ...] = ()
    associations: tuple[AssociationLabel, ...] = ()
    # Properties the tenant's app reads but another system owns (checked by preflight, never created here).
    requires: tuple[tuple[str, str], ...] = field(default=())

    def object(self, name: str) -> ObjectModel:
        for obj in self.objects:
            if obj.name == name:
                return obj
        raise KeyError(name)

    def problems(self) -> list[str]:
        """Every rule this model breaks. An empty list means it compiles to HubSpot and to Salesforce."""
        found: list[str] = []
        names = [o.name for o in self.objects]
        found += [f"object {n} is declared twice" for n in sorted({n for n in names if names.count(n) > 1})]
        for obj in self.objects:
            found += _object_problems(obj)
        known = set(names) | set(STANDARD_OBJECTS)
        for pipeline in self.pipelines:
            where = f"pipeline {pipeline.object_name}/{pipeline.label}"
            owner = next((o for o in self.objects if o.name == pipeline.object_name), None)
            if pipeline.object_name not in PIPELINE_OBJECTS and not (owner and owner.custom and owner.has_pipeline):
                found.append(f"{where}: {pipeline.object_name} cannot have a pipeline")
            labels = [s.label for s in pipeline.stages]
            if len(set(labels)) != len(labels):
                found.append(f"{where}: stage labels repeat")
            if not any(s.closed for s in pipeline.stages) or all(s.closed for s in pipeline.stages):
                found.append(f"{where}: needs at least one open and one closed stage")
            for stage in pipeline.stages:
                deal = pipeline.object_name == "deals"
                if deal and (stage.probability is None or not 0 <= stage.probability <= 1):
                    found.append(f"{where}: deal stage {stage.label} needs a probability between 0 and 1")
                if not deal and stage.probability is not None:
                    found.append(f"{where}: only deal stages carry a probability ({stage.label})")
                if deal and stage.closed and stage.probability not in (0.0, 1.0):
                    found.append(f"{where}: closed deal stage {stage.label} must be won (1) or lost (0)")
        pairs: dict[tuple[str, str], list[str]] = {}
        for assoc in self.associations:
            where = f"association {assoc.from_object}->{assoc.to_object} {assoc.name}"
            for end in (assoc.from_object, assoc.to_object):
                if end not in known:
                    found.append(f"{where}: unknown object {end}")
            if not _name_ok(assoc.name):
                found.append(f"{where}: name must be lower snake case, at most {MAX_NAME} characters")
            pairs.setdefault((assoc.from_object, assoc.to_object), []).append(assoc.name)
        for pair, pair_names in pairs.items():
            if len(set(pair_names)) != len(pair_names):
                found.append(f"association {pair[0]}->{pair[1]}: a label name repeats")
        for object_name, prop in self.requires:
            if object_name not in known:
                found.append(f"requires {object_name}.{prop}: unknown object")
        return found


def _name_ok(name: str) -> bool:
    return bool(NAME.match(name)) and len(name) <= MAX_NAME and not name.startswith("hs_")


def _object_problems(obj: ObjectModel) -> list[str]:
    found: list[str] = []
    where = f"object {obj.name}"
    if obj.custom:
        if obj.name in STANDARD_OBJECTS or not _name_ok(obj.name):
            found.append(f"{where}: a custom object needs its own lower snake case name")
        if not (obj.singular and obj.plural):
            found.append(f"{where}: a custom object needs singular and plural labels")
        if obj.primary_display not in obj.names:
            found.append(f"{where}: primary display property {obj.primary_display!r} is not declared")
        for attr in ("secondary_display", "searchable", "required"):
            missing = [n for n in getattr(obj, attr) if n not in obj.names]
            if missing:
                found.append(f"{where}: {attr} names undeclared properties {missing}")
        bad = [o for o in obj.associated_objects if o not in STANDARD_OBJECTS]
        if bad:
            found.append(f"{where}: custom objects associate with standard objects here, not {bad}")
    elif obj.name not in STANDARD_OBJECTS:
        found.append(f"{where}: not a standard object; declare it custom=True")
    elif obj.has_pipeline:
        found.append(f"{where}: has_pipeline is for custom objects; deals and tickets always have pipelines")
    if not _name_ok(obj.group):
        found.append(f"{where}: group {obj.group!r} must be lower snake case, at most {MAX_NAME} characters")
    names = obj.names
    found += [f"{where}: property {n} is declared twice" for n in sorted({n for n in names if names.count(n) > 1})]
    if KEY_PROPERTY not in names:
        found.append(f"{where}: every object carries {KEY_PROPERTY}")
    else:
        key = obj.prop(KEY_PROPERTY)
        should_be_unique = obj.custom or obj.name in UNIQUE_CAPABLE
        if key.type != "string" or key.unique != should_be_unique:
            found.append(f"{where}: {KEY_PROPERTY} must be a string, unique exactly where HubSpot allows it")
    if sum(p.unique for p in obj.properties) > MAX_UNIQUE_PER_OBJECT:
        found.append(f"{where}: more than {MAX_UNIQUE_PER_OBJECT} unique properties")
    for prop in obj.properties:
        at = f"{where}.{prop.name}"
        if not _name_ok(prop.name):
            found.append(f"{at}: name must be lower snake case, at most {MAX_NAME} characters, not hs_")
        if prop.type not in FIELD_TYPES or prop.field_type not in FIELD_TYPES[prop.type]:
            found.append(f"{at}: field type {prop.field_type!r} does not fit type {prop.type!r}")
        if prop.unique and (prop.type != "string" or not (obj.custom or obj.name in UNIQUE_CAPABLE)):
            found.append(f"{at}: only string properties on {sorted(UNIQUE_CAPABLE)} or custom objects are unique")
        if prop.type == "enumeration":
            values = [o.value for o in prop.options]
            if not values:
                found.append(f"{at}: an enumeration needs options")
            if len(set(values)) != len(values):
                found.append(f"{at}: option values repeat")
            if any(not re.match(r"^[A-Za-z0-9_\-]+$", v) for v in values):
                found.append(f"{at}: option values must be plain identifiers")
        elif prop.options:
            found.append(f"{at}: only enumerations carry options")
    return found


def options(*pairs: tuple[str, str]) -> tuple[Option, ...]:
    return tuple(Option(value, label) for value, label in pairs)


def slug(text: str) -> str:
    """An option value or key fragment from a label: ``"Second-level review"`` → ``"second_level_review"``."""
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", text.lower())).strip("_")


def enum_of(labels: list[str] | tuple[str, ...]) -> tuple[Option, ...]:
    """Options for a set of source values, in first-seen order, keyed by their slug."""
    seen: dict[str, str] = {}
    for label in labels:
        seen.setdefault(slug(label), label)
    return tuple(Option(value, label) for value, label in seen.items())


def key_property(object_name: str, *, custom: bool = False) -> Property:
    return Property(KEY_PROPERTY, "CRM platform key", "string", "text",
                    "The source system's identifier. The CRM platform matches records by it; never edit it by hand.",
                    unique=custom or object_name in UNIQUE_CAPABLE)
