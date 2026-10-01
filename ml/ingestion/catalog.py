"""Source-file ingestion - the mapping from a raw file to a SaltLens record.

This is the layer that answers "what is this column, in SaltLens terms, and is it
allowed to become a record at all". It exists because the backend already owns a
precise vocabulary for what a pan observation, a weather reading and a recorded
outcome are, and because nothing anywhere owned the other half of the problem:
getting a researcher's spreadsheet into that vocabulary without quietly turning a
guess into a measurement.

**The vocabulary is imported, not retyped.** Every canonical field name, unit and
plausibility bound below is read from the pydantic models the application itself
stores through, and from the catalogues in ``app.services`` that already name each
quantity's source collection, source field and leakage class. A second copy of
those names would be a second set of conclusions about what the data means, and
the two would drift the first time a field was renamed. So the field specs are
built from the live models, and the aliases are only the names a *source file*
might plausibly use, which is genuinely this module's own concern.

**The one rule that is not a mapping.** :class:`ProvenanceCategory` is checked
against the destination collection, and a file whose rows are not measurements is
refused before anything is written. ``pan_observations`` stores what somebody
measured; a simulated or physics-derived row is a different kind of statement and
the model that describes it already exists (``SimulationRun``, and the
``physics_predicted_*`` features in ``dataset_catalog``). Letting a computed
figure land in a measurement collection is precisely the substitution that makes a
dataset unfalsifiable, so it is a hard rejection rather than a warning, and the
rejection names the category that caused it.

**Content-addressed identifiers.** Backfilled observations legitimately share a
timestamp, so ``pan_observations`` cannot be made unique on ``(pan_id, timestamp)``
and is deliberately not (see ``app/db/indexes.py``). Idempotency therefore cannot
come from the database. It comes from here: the identifier of a staged record is a
digest of its own content, so re-ingesting the same file produces the same
identifiers and the second run can recognise what it already stored. The same
pattern the project already uses for evaluation ids.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The backend is a sibling package, not an installed distribution, so it goes on
#: the path explicitly. Done here rather than at the call site because forgetting
#: it should produce one clear message rather than a stack of ImportErrors from
#: somewhere deeper. Mirrors ``ml/training/pipeline.py``.
_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def bind_backend_env() -> bool:
    """Point the shared settings at ``backend/.env`` before the backend loads.

    ``Settings`` declares ``env_file=".env"``, and pydantic-settings resolves that
    against the working directory. The API is started from ``backend/``, so it finds
    the file; a command run from the repository root would not, and would then
    report the database as unconfigured and refuse to import. Rather than make the
    caller copy a connection string out of the file by hand, this names that same
    file explicitly.

    It runs here, at the top of the module, because that is the only point where
    the backend is not yet imported. ``app.db.__init__`` eagerly imports the
    database manager, and ``app.config`` binds its ``settings`` name at import time,
    so a call made after the first ``app.*`` import would be rebuilding an object
    that had already been handed out.

    Only the path is changed. The values, the precedence and the single source of
    configuration remain the application's own, so there is no second place where a
    database can be configured and therefore no way for the importer and the API to
    end up pointed at different clusters.

    Returns whether a file was found, so a caller can say so rather than leaving
    "not_configured" to be interpreted.
    """
    import os

    import app.config as config_module

    # The suite sets this before anything imports the application, and it exists so
    # that no test can reach a real cluster. Reading ``backend/.env`` here would hand
    # a live URI to a test process, which is the opposite of what that variable is
    # for, so the file is left alone when it is set.
    if os.environ.get("SALTLENS_TEST_NO_DATABASE"):
        return False

    env = _BACKEND / ".env"
    if not env.is_file():
        return False
    config_module.Settings.model_config["env_file"] = str(env)
    config_module.get_settings.cache_clear()
    config_module.settings = config_module.get_settings()
    return True


#: Whether this run found a backend ``.env``, for a caller that has to explain an
#: unconfigured database rather than silently doing nothing.
BACKEND_ENV_FOUND = bind_backend_env()

from app.db.collections import (
    ACTUAL_OUTCOMES,
    PAN_OBSERVATIONS,
    SALT_PANS,
    WEATHER_OBSERVATIONS,
)
from app.models.actual_outcome import ActualOutcome
from app.models.base import normalize_pan_id
from app.models.enums import (
    HarvestOutcome,
    ResearchDataSource,
    WeatherSource,
)
from app.models.pan_observation import PanObservation
from app.models.salt_pan import RiskThresholds, SaltPan
from app.models.weather import WeatherObservation

#: The fixed sentence printed when a file declares no usable provenance. Matched on
#: by ``import`` and by the tests, so it is a constant rather than an assembled
#: string: an assembled one would drift the first time someone wanted to name a
#: column in it.
PROVENANCE_REQUIRED_MESSAGE = (
    "Source provenance is required: declare how these rows were obtained."
)

#: How a canonical field's value is read out of a source cell.
#:
#: Declared per field rather than inferred from the field's name, because the
#: inference is exactly the kind of guess this package exists to avoid: a suffix
#: rule that decides ``pan_id`` is numeric would reject every row of every file,
#: and a suffix rule that missed a field would store a label where a measurement
#: belongs. One declaration, read by both the audit and the validator, so the two
#: cannot disagree about what a column is supposed to hold.
VALUE_NUMERIC = "numeric"
VALUE_INSTANT = "instant"
VALUE_TEXT = "text"
VALUE_KINDS = (VALUE_NUMERIC, VALUE_INSTANT, VALUE_TEXT)


class ProvenanceCategory:
    """How a source file's rows came to exist.

    Not an enum, deliberately: it is a plain tuple of constants so the values can
    be used directly as argument choices and printed in a report without a
    ``.value`` dance, which is how the rest of this module treats them. The
    vocabulary is the one the research-integrity policy names, and it is closed:
    a new category is a policy change, not a convenience.
    """

    #: Published measurements from an external, citable source.
    PUBLIC_MEASURED = "PUBLIC_MEASURED"
    #: Measured in the field by the research team.
    FIELD_MEASURED = "FIELD_MEASURED"
    #: Measured by an instrument, reported by a sensor gateway or log.
    SENSOR_MEASURED = "SENSOR_MEASURED"
    #: Computed from a physical model, not measured.
    PHYSICS_DERIVED = "PHYSICS_DERIVED"
    #: Produced by a simulation or a synthetic generator.
    SIMULATED = "SIMULATED"
    #: Not established. Permitted as a declaration, refused as a measurement.
    UNKNOWN = "UNKNOWN"

    ALL: tuple[str, ...] = (
        PUBLIC_MEASURED,
        FIELD_MEASURED,
        SENSOR_MEASURED,
        PHYSICS_DERIVED,
        SIMULATED,
        UNKNOWN,
    )

    #: The categories that are statements about what happened to a pan.
    MEASURED: frozenset[str] = frozenset(
        {PUBLIC_MEASURED, FIELD_MEASURED, SENSOR_MEASURED}
    )

    #: The categories that are computations. These may never be written to a
    #: measurement collection.
    DERIVED: frozenset[str] = frozenset({PHYSICS_DERIVED, SIMULATED})

    @classmethod
    def is_measured(cls, value: str) -> bool:
        return value in cls.MEASURED


def normalize_column(name: str) -> str:
    """Fold a source column name to the form the catalogue matches on.

    Case, surrounding whitespace, spaces, hyphens and dots are all noise between a
    spreadsheet's header and a field name, and treating them as significant would
    make an audit report "unknown column: pan id" for a file that plainly has one.
    """
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


@dataclass(frozen=True)
class FieldSpec:
    """One canonical field, and the source names that may stand for it."""

    name: str
    required: bool = False
    unit: str | None = None
    aliases: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    #: Whether :attr:`minimum` and :attr:`maximum` are exclusive, mirroring the
    #: model's own ``gt``/``lt`` constraints rather than assuming inclusiveness.
    strict_minimum: bool = False
    strict_maximum: bool = False
    #: The vocabulary a source value must belong to, when the field is closed.
    vocabulary: tuple[str, ...] | None = None
    note: str | None = None
    #: One of :data:`VALUE_KINDS`. Drives both the audit's range checks and the
    #: validator's coercion, so a text field is never checked for being a number.
    value_kind: str = VALUE_TEXT
    #: Set when the field is a nested pydantic model rather than a scalar. The
    #: model is named so the audit and the validator can read the inner bounds
    #: instead of guessing them.
    nested_model: type[Any] | None = None
    #: Flat source columns that assemble one field of :attr:`nested_model`, as
    #: ``(column, inner_field)`` pairs. A spreadsheet export puts the four risk
    #: thresholds in four columns; requiring a single JSON column would reject
    #: the format operators actually export.
    components: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.value_kind not in VALUE_KINDS:
            raise ValueError(
                f"{self.name}: unknown value kind {self.value_kind!r}; expected one "
                "of " + ", ".join(VALUE_KINDS)
            )
        if self.components and self.nested_model is None:
            raise ValueError(f"{self.name}: components need a nested_model to fill")
        if self.components:
            unknown = [
                inner for _, inner in self.components if inner not in self.inner_fields
            ]
            if unknown:
                raise ValueError(
                    f"{self.name}: no such field(s) on "
                    f"{self.nested_model.__name__}: " + ", ".join(unknown)
                )

    def accepts(self, column: str) -> bool:
        return normalize_column(column) in self.source_names

    @property
    def inner_fields(self) -> tuple[str, ...]:
        if self.nested_model is None:
            return ()
        return tuple(self.nested_model.model_fields)

    @property
    def source_names(self) -> frozenset[str]:
        return frozenset(
            {normalize_column(self.name)}
            | {normalize_column(a) for a in self.aliases}
            | {normalize_column(column) for column, _ in self.components}
        )


@dataclass(frozen=True)
class RecordSpec:
    """One destination collection and the shape a source file must have to fill it."""

    name: str
    collection: str
    model: type[Any]
    #: Fields that must be present and non-empty on every row.
    fields: tuple[FieldSpec, ...]
    #: The field the provenance declaration is read from, if any.
    provenance_column: str | None = None
    #: The prefix of the content-addressed identifier, matching the project's ids.
    id_prefix: str = "rec"
    #: Fields that make up the content digest, in a fixed order.
    digest_fields: tuple[str, ...] = ()
    note: str = ""

    @property
    def required_fields(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.required)

    @property
    def by_name(self) -> dict[str, FieldSpec]:
        return {f.name: f for f in self.fields}

    def resolve(self, column: str) -> str | None:
        """The canonical field a source column maps to, or ``None`` if it is not ours.

        A flat component column of a nested field resolves to the *nested* field
        rather than to the inner name. The inner name is not a top-level field and
        the audit's per-column reporting works in top-level fields, so folding the
        component back onto its parent is what keeps it from being reported as an
        unmapped column. :attr:`component_field` recovers which inner field it was.
        """
        folded = normalize_column(column)
        for candidate in self.fields:
            if folded in candidate.source_names:
                return candidate.name
        return None

    def component_field(self, column: str) -> tuple[str, str] | None:
        """``(nested_field, inner_field)`` for a flat component column, else ``None``."""
        folded = normalize_column(column)
        for candidate in self.fields:
            for source_column, inner in candidate.components:
                if normalize_column(source_column) == folded:
                    return candidate.name, inner
        return None

    def unknown_columns(self, columns: Iterable[str]) -> tuple[str, ...]:
        return tuple(sorted(c for c in columns if self.resolve(c) is None))


def _model_bounds(
    model: type[Any], field_name: str
) -> tuple[float | None, float | None, bool, bool]:
    """``(minimum, maximum, minimum_is_strict, maximum_is_strict)`` for a model field.

    The strictness is reported rather than assumed. Pydantic distinguishes ``gt=0``
    from ``ge=0``, and a validator refuses ``0`` for the first and accepts it for the
    second. Collapsing both into an inclusive minimum would let the audit pass a
    value the model then rejects, which is the one thing an audit exists to prevent.
    """
    field = model.model_fields[field_name]
    minimum: float | None = None
    maximum: float | None = None
    strict_min = strict_max = False
    for entry in field.metadata:
        ge = getattr(entry, "ge", None)
        gt = getattr(entry, "gt", None)
        le = getattr(entry, "le", None)
        lt = getattr(entry, "lt", None)
        if ge is not None:
            minimum = float(ge)
            strict_min = False
        elif gt is not None:
            minimum = float(gt)
            strict_min = True
        if le is not None:
            maximum = float(le)
            strict_max = False
        elif lt is not None:
            maximum = float(lt)
            strict_max = True
    return minimum, maximum, strict_min, strict_max


#: Every field that carries a measurement, with the plausible range the model
#: already enforces. Quoted from the models rather than retyped: if a bound is
#: widened in the application, the audit below follows it, and a file that used to
#: be rejected is not silently accepted against a stale copy of the old range.
#:
#: These are wide on purpose. The models document them as catching data-entry
#: mistakes such as ``28500`` g/L rather than policing real measurements, and the
#: audit inherits that intent. A value outside the range is reported as a
#: *plausibility* finding for a human to judge, never silently clipped.
_PAN_SPEC_BOUNDS: dict[str, tuple[float | None, float | None]] = {
    "latitude": _model_bounds(SaltPan, "latitude"),
    "longitude": _model_bounds(SaltPan, "longitude"),
    "area_m2": _model_bounds(SaltPan, "area_m2"),
    "normal_depth_cm": _model_bounds(SaltPan, "normal_depth_cm"),
    "target_harvest_salinity_g_l": _model_bounds(
        SaltPan, "target_harvest_salinity_g_l"
    ),
}

_OBSERVATION_SPEC_BOUNDS: dict[str, tuple[float | None, float | None]] = {
    "salinity_g_l": _model_bounds(PanObservation, "salinity_g_l"),
    "water_depth_cm": _model_bounds(PanObservation, "water_depth_cm"),
    "brine_temperature_c": _model_bounds(PanObservation, "brine_temperature_c"),
    "humidity_percent": _model_bounds(PanObservation, "humidity_percent"),
    "ambient_temperature_c": _model_bounds(PanObservation, "ambient_temperature_c"),
}

_WEATHER_SPEC_BOUNDS: dict[str, tuple[float | None, float | None]] = {
    "rainfall_mm": _model_bounds(WeatherObservation, "rainfall_mm"),
    "rainfall_probability_percent": _model_bounds(
        WeatherObservation, "rainfall_probability_percent"
    ),
    "ambient_temperature_c": _model_bounds(WeatherObservation, "ambient_temperature_c"),
    "humidity_percent": _model_bounds(WeatherObservation, "humidity_percent"),
}

_OUTCOME_SPEC_BOUNDS: dict[str, tuple[float | None, float | None]] = {
    "forecast_rainfall_mm": _model_bounds(ActualOutcome, "forecast_rainfall_mm"),
    "pre_event_salinity_g_l": _model_bounds(ActualOutcome, "pre_event_salinity_g_l"),
    "pre_event_depth_cm": _model_bounds(ActualOutcome, "pre_event_depth_cm"),
    "actual_rainfall_mm": _model_bounds(ActualOutcome, "actual_rainfall_mm"),
    "post_event_salinity_g_l": _model_bounds(
        ActualOutcome, "post_event_salinity_g_l"
    ),
    "post_event_depth_cm": _model_bounds(ActualOutcome, "post_event_depth_cm"),
}


def _with_bounds(
    fields: Iterable[FieldSpec],
    bounds: dict[str, tuple[float | None, float | None]],
    model: type[Any] | None = None,
) -> tuple[FieldSpec, ...]:
    """Attach each field's declared kind and the model's own bounds.

    Both are read from tables declared just above rather than retyped on every
    field. A field appears in exactly one of :data:`_NUMERIC_FIELDS` and
    :data:`_INSTANT_FIELDS`, and a field in neither is text -- so the whole schema's
    typing is readable in one place, and a field cannot be declared numeric here
    and treated as text by the validator.
    """
    return tuple(
        FieldSpec(
            name=spec.name,
            required=spec.required,
            unit=spec.unit,
            aliases=spec.aliases,
            minimum=bounds.get(spec.name, (None, None))[0],
            maximum=bounds.get(spec.name, (None, None))[1],
            strict_minimum=(
                _model_bounds(model, spec.name)[2]
                if model is not None and spec.name in model.model_fields
                else False
            ),
            strict_maximum=(
                _model_bounds(model, spec.name)[3]
                if model is not None and spec.name in model.model_fields
                else False
            ),
            vocabulary=spec.vocabulary,
            note=spec.note,
            value_kind=(
                VALUE_NUMERIC
                if spec.name in _NUMERIC_FIELDS
                else VALUE_INSTANT
                if spec.name in _INSTANT_FIELDS
                else VALUE_TEXT
            ),
            nested_model=spec.nested_model,
            components=spec.components,
        )
        for spec in fields
    )


#: Every canonical field that carries a quantity, and the field that carries an
#: instant. Everything else is text.
#:
#: Declared as two tables so the schema's typing is readable in one place and so a
#: field cannot end up typed one way for the audit and another for the validator.
#: A field in neither table is text, which is the safe default: text is never range
#: checked and never coerced to a number.
_NUMERIC_FIELDS: frozenset[str] = frozenset(
    {
        # Module 3 readings
        "salinity_g_l",
        "water_depth_cm",
        "brine_temperature_c",
        "humidity_percent",
        "ambient_temperature_c",
        # Module 4 weather
        "rainfall_mm",
        "rainfall_probability_percent",
        # Module 2 registration
        "latitude",
        "longitude",
        "area_m2",
        "normal_depth_cm",
        "target_harvest_salinity_g_l",
        # Module 9 outcomes
        "forecast_rainfall_mm",
        "pre_event_salinity_g_l",
        "pre_event_depth_cm",
        "actual_rainfall_mm",
        "post_event_salinity_g_l",
        "post_event_depth_cm",
    }
)

_INSTANT_FIELDS: frozenset[str] = frozenset({"timestamp", "forecast_for"})


# ---------------------------------------------------------------------------
# Record specifications
# ---------------------------------------------------------------------------

#: A single operator-entered or instrument reading of a pan's condition.
#:
#: ``data_source`` is deliberately *not* a mappable column. It records how the
#: reading reached SaltLens, and letting a source file assert it would let a file
#: claim to be a sensor feed. The ingestion layer sets it from the declared
#: provenance category instead, which is the one thing the operator is actually in
#: a position to attest to.
OBSERVATION_SPEC = RecordSpec(
    name="observations",
    collection=PAN_OBSERVATIONS,
    model=PanObservation,
    provenance_column=None,
    id_prefix="obs",
    digest_fields=(
        "pan_id",
        "timestamp",
        "salinity_g_l",
        "water_depth_cm",
        "brine_temperature_c",
        "humidity_percent",
        "ambient_temperature_c",
    ),
    note="A measured reading of one pan at one instant.",
    fields=_with_bounds(
        (
            FieldSpec("pan_id", required=True, aliases=("pan", "panid", "pan code")),
            FieldSpec("timestamp", required=True, unit="UTC", aliases=("observed_at", "recorded_at", "datetime", "time")),
            FieldSpec("salinity_g_l", unit="g/L", aliases=("salinity", "salt", "salinity_gl", "g_l")),
            FieldSpec("water_depth_cm", unit="cm", aliases=("depth_cm", "water depth", "depth")),
            FieldSpec("brine_temperature_c", unit="degC", aliases=("brine_temp_c", "water_temperature_c", "brine temperature")),
            FieldSpec("humidity_percent", unit="percent", aliases=("humidity", "rh", "relative_humidity_percent")),
            FieldSpec("ambient_temperature_c", unit="degC", aliases=("air_temperature_c", "ambient_temp_c")),
            FieldSpec("note"),
        ),
        _OBSERVATION_SPEC_BOUNDS,
        PanObservation,
    ),
)

WEATHER_SPEC = RecordSpec(
    name="weather",
    collection=WEATHER_OBSERVATIONS,
    model=WeatherObservation,
    provenance_column=None,
    id_prefix="wx",
    digest_fields=(
        "pan_id",
        "timestamp",
        "forecast_for",
        "rainfall_mm",
        "rainfall_probability_percent",
        "ambient_temperature_c",
        "humidity_percent",
    ),
    note="A measured or forecast reading of the sky over one pan.",
    fields=_with_bounds(
        (
            FieldSpec("pan_id", required=True, aliases=("pan", "panid")),
            FieldSpec("timestamp", required=True, unit="UTC", aliases=("observed_at", "recorded_at", "datetime", "time", "forecast_time")),
            FieldSpec("forecast_for", unit="UTC", aliases=("valid_at", "forecast_time", "applies_at")),
            FieldSpec("rainfall_mm", unit="mm", aliases=("rainfall", "rain_mm", "precipitation_mm", "rain")),
            FieldSpec(
                "rainfall_probability_percent",
                unit="percent",
                aliases=(
                    "precipitation_probability_percent",
                    "pop",
                    "rain_probability_percent",
                    "rain_probability",
                    "chance_of_rain",
                ),
            ),
            FieldSpec("ambient_temperature_c", unit="degC", aliases=("air_temperature_c", "ambient_temp_c", "temperature_c")),
            FieldSpec("humidity_percent", unit="percent", aliases=("humidity", "rh", "relative_humidity_percent")),
        ),
        _WEATHER_SPEC_BOUNDS,
        WeatherObservation,
    ),
)

PAN_SPEC = RecordSpec(
    name="pans",
    collection=SALT_PANS,
    model=SaltPan,
    provenance_column=None,
    id_prefix="pan",
    digest_fields=(),
    note="Site registration. Metadata about a pan rather than a reading from it.",
    fields=_with_bounds(
        (
            FieldSpec("pan_id", required=True, aliases=("pan", "panid", "pan code")),
            FieldSpec("pan_name", required=True, aliases=("name",)),
            FieldSpec("location_name", required=True, aliases=("location", "site", "site_name")),
            FieldSpec("latitude", unit="deg", aliases=("lat",)),
            FieldSpec("longitude", unit="deg", aliases=("lon", "lng", "long")),
            FieldSpec("area_m2", required=True, unit="m2", aliases=("area", "area_m2sq")),
            FieldSpec(
                "normal_depth_cm",
                required=True,
                unit="cm",
                aliases=("normal_depth", "design_depth_cm", "depth_cm", "depth"),
            ),
            FieldSpec(
                "target_harvest_salinity_g_l",
                required=True,
                unit="g/L",
                aliases=("target_salinity_g_l", "harvest_target_salinity_g_l"),
            ),
            # Required by the model, but every threshold inside it is optional, so
            # "no site-specific thresholds recorded" is a truthful thing to store and
            # a refusal would only block registering a pan that is otherwise real.
            # An absent column therefore becomes an empty threshold set, never a
            # guessed one; the audit raises a warning so it cannot pass unnoticed,
            # because the thresholds gate the Module 08 and 09 thresholds.
            FieldSpec(
                "risk_thresholds",
                nested_model=RiskThresholds,
                components=(
                    ("flood_depth_cm", "flood_depth_cm"),
                    ("flood depth (cm)", "flood_depth_cm"),
                    ("dilution_salinity_g_l", "dilution_salinity_g_l"),
                    ("dilution salinity (g/l)", "dilution_salinity_g_l"),
                    ("critical_rainfall_24h_mm", "critical_rainfall_24h_mm"),
                    ("critical rainfall 24h (mm)", "critical_rainfall_24h_mm"),
                    ("target_flow_cm", "target_flow_cm"),
                    ("target flow (cm)", "target_flow_cm"),
                ),
            ),
        ),
        _PAN_SPEC_BOUNDS,
        SaltPan,
    ),
)

OUTCOME_SPEC = RecordSpec(
    name="outcomes",
    collection=ACTUAL_OUTCOMES,
    model=ActualOutcome,
    provenance_column=None,
    id_prefix="outcome",
    digest_fields=(
        "pan_id",
        "event_id",
        "timestamp",
        "actual_rainfall_mm",
        "post_event_salinity_g_l",
        "post_event_depth_cm",
        "harvest_outcome",
    ),
    note="A recorded post-event outcome. The only source of a post-anchor label.",
    fields=_with_bounds(
        (
            FieldSpec("pan_id", required=True, aliases=("pan", "panid")),
            FieldSpec("event_id", required=True, aliases=("event", "eventid", "storm_id")),
            FieldSpec("timestamp", unit="UTC", aliases=("recorded_at", "observed_at", "datetime", "time", "outcome_timestamp")),
            FieldSpec("forecast_rainfall_mm", unit="mm", aliases=("forecast_rain_mm",)),
            FieldSpec("pre_event_salinity_g_l", unit="g/L", aliases=("pre_salinity_g_l", "pre_event_salinity")),
            FieldSpec("pre_event_depth_cm", unit="cm", aliases=("pre_depth_cm",)),
            FieldSpec("actual_rainfall_mm", unit="mm", aliases=("actual_rain_mm", "rainfall_mm", "observed_rainfall_mm")),
            FieldSpec("post_event_salinity_g_l", unit="g/L", aliases=("post_salinity_g_l", "post_event_salinity")),
            FieldSpec("post_event_depth_cm", unit="cm", aliases=("post_depth_cm",)),
            FieldSpec("operator_action", aliases=("action", "operator_notes")),
            FieldSpec(
                "harvest_outcome",
                vocabulary=tuple(member.value for member in HarvestOutcome),
                aliases=("outcome", "harvest_result"),
                note="A recorded 'none' is an observation; an empty cell is not.",
            ),
        ),
        _OUTCOME_SPEC_BOUNDS,
        ActualOutcome,
    ),
)

#: Every file family the pipeline understands, keyed by the ``--kind`` value.
RECORD_SPECS: dict[str, RecordSpec] = {
    spec.name: spec
    for spec in (PAN_SPEC, OBSERVATION_SPEC, WEATHER_SPEC, OUTCOME_SPEC)
}


def data_source_for(category: str) -> ResearchDataSource:
    """The ``data_source`` a declared provenance category implies on a reading.

    Only measured categories reach here; :func:`require_measurement_provenance`
    refuses the rest before this is called. An instrument reading is stored as
    ``sensor`` because that is what distinguishes it downstream, and a hand or
    spreadsheet reading is stored as ``import`` so that a row which came from a
    file can always be told apart from one an operator typed into the form.
    """
    if category == ProvenanceCategory.SENSOR_MEASURED:
        return ResearchDataSource.SENSOR
    if ProvenanceCategory.is_measured(category):
        return ResearchDataSource.IMPORT
    raise ValueError(
        f"provenance category {category!r} is not a measurement and cannot be "
        "stored as the data source of a reading"
    )


def weather_source_for(category: str) -> WeatherSource:
    """The ``source`` a declared provenance category implies on a weather reading.

    Public weather data and field readings both arrive as files, and the weather
    model has no ``import`` member: its vocabulary is ``manual``, ``weather_api``
    and ``sensor``. A file-provided reading is therefore filed as ``weather_api``
    when the category is public, and as ``manual`` when a person transcribed it.
    That is a loss of resolution the model already accepts, and it is better than
    adding a member to a closed vocabulary to hold a filename.
    """
    if category == ProvenanceCategory.SENSOR_MEASURED:
        return WeatherSource.SENSOR
    if category == ProvenanceCategory.PUBLIC_MEASURED:
        return WeatherSource.WEATHER_API
    if category == ProvenanceCategory.FIELD_MEASURED:
        return WeatherSource.MANUAL
    raise ValueError(
        f"provenance category {category!r} is not a measurement and cannot be "
        "stored as the source of a weather reading"
    )


def require_measurement_provenance(category: str | None) -> str:
    """Return a category, or refuse.

    The refusal is split in two because the two failures mean different things to
    whoever has to fix them. An absent category is a missing declaration and the
    file may well be real; a derived or simulated category is a file that is
    saying, in so many words, that it is not a record of what happened, and
    writing it to ``pan_observations`` would put a computed figure in a collection
    whose every reader assumes a measurement.
    """
    if category is None:
        raise ValueError(PROVENANCE_REQUIRED_MESSAGE)
    if category not in ProvenanceCategory.ALL:
        raise ValueError(
            f"unknown provenance category {category!r}; expected one of: "
            + ", ".join(ProvenanceCategory.ALL)
        )
    if category in ProvenanceCategory.DERIVED:
        raise ValueError(
            f"provenance category {category!r} is a computation, not a measurement, "
            "so these rows cannot be written to a measurement collection. A physics "
            "prediction belongs in a SimulationRun, and a simulated row belongs in "
            "an experiment fixture that says so on every row."
        )
    if category == ProvenanceCategory.UNKNOWN:
        raise ValueError(
            "provenance category 'UNKNOWN' is not a measurement. Declare how the "
            "rows were obtained, or leave the file out of the research database."
        )
    return category


def id_field(spec: RecordSpec) -> str:
    """The identifier field a model names, which differs per collection.

    Read from the model rather than kept in a table here, so a collection that
    renamed its identifier cannot leave the ingestion layer writing to a field that
    no longer exists -- or, worse, deduplicating on a field nothing is indexed by.
    """
    for candidate in ("observation_id", "weather_id", "outcome_id", "pan_id"):
        if candidate in spec.model.model_fields:
            return candidate
    raise ValueError(
        f"{spec.name}: no known identifier field on {spec.model.__name__}; the "
        "ingestion catalogue must be updated rather than guessing"
    )


def provenance_field(spec: RecordSpec) -> str | None:
    """The field a model records a reading's origin in, if it has one.

    Read off the model rather than held in a table here, for the same reason
    :func:`id_field` is: a collection that renamed or dropped the field would
    otherwise leave the staged file asserting a provenance the model no longer
    stores. ``SaltPan`` has neither field, which is correct -- site registration is
    a statement about a pan, not a reading taken from one.
    """
    for candidate in ("data_source", "source"):
        if candidate in spec.model.model_fields:
            return candidate
    return None


def canonical_row(spec: RecordSpec, row: dict[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Fold a source row onto the canonical field names.

    Returns the canonical dict and the source columns that matched nothing. Unknown
    columns are reported rather than dropped silently, because a column named
    ``salinity`` in a file whose author believed it was grams per litre is exactly
    the kind of thing an audit has to surface, and dropping it would leave the
    resulting row quietly missing a measurement.
    """
    canonical: dict[str, Any] = {}
    unknown: list[str] = []
    nested: dict[str, dict[str, Any]] = {}
    for column, value in row.items():
        target = spec.resolve(column)
        if target is None:
            unknown.append(column)
            continue
        component = spec.component_field(column)
        if component is not None:
            # A flat column feeding a nested model. Collected under the inner field
            # name and folded into the object once the row has been walked, because
            # the object may also arrive whole in a single JSON column.
            nested.setdefault(component[0], {})[component[1]] = value
            continue
        canonical.setdefault(target, value)

    # Assemble every nested field the spec declares, not only the ones a flat
    # component column happened to feed. A staged file carries the object whole in
    # one column and has no component columns at all, so driving this loop from the
    # component map would skip it and leave the JSON string unparsed.
    for field_name, field_spec in spec.by_name.items():
        if field_spec.nested_model is None:
            continue
        parts = nested.get(field_name, {})
        whole = canonical.get(field_name)
        assembled: dict[str, Any] = {}
        if isinstance(whole, dict):
            assembled.update(whole)
        elif isinstance(whole, str) and whole.strip().startswith("{"):
            # A JSON column is the other way operators publish the same values. A
            # parse failure is not guessed at; the validator reports it, and any
            # flat columns still contribute what they carried.
            try:
                decoded = json.loads(whole)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                assembled.update(decoded)
        assembled.update(parts)
        assembled = {k: v for k, v in assembled.items() if k in field_spec.inner_fields}
        canonical[field_name] = assembled
    return canonical, tuple(unknown)


def normalize_pan(value: str) -> str:
    """Apply the application's own pan-identifier normalisation.

    Reused rather than reimplemented so a file cannot be accepted under a pan id
    that the API would have rejected as a different pan.
    """
    return normalize_pan_id(value)


def content_id(spec: RecordSpec, canonical: dict[str, Any]) -> str:
    """A deterministic identifier for a staged record, derived from its content.

    This is what makes re-ingestion safe. The digest covers exactly the fields
    listed in ``digest_fields`` and nothing else, in a fixed order, with missing
    values rendered as an empty string so that a row and a row with an explicit
    null hash identically -- otherwise an absent measurement and a written-out
    ``null`` would produce two identifiers for one physical reading.
    """
    parts: list[str] = []
    for name in spec.digest_fields:
        value = canonical.get(name)
        if value is None:
            parts.append("")
        elif isinstance(value, float):
            # ``repr`` round-trips a float exactly, so 0.1 and 0.1 agree.
            parts.append(repr(value))
        elif isinstance(value, dict):
            # A nested object (the pan's risk thresholds) is rendered with sorted
            # keys so that two files holding the same thresholds in a different
            # column order produce one identifier rather than two.
            parts.append(
                json.dumps(
                    {k: value[k] for k in sorted(value)},
                    sort_keys=True,
                    default=str,
                )
            )
        else:
            parts.append(str(value))
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{spec.id_prefix}-{digest[:24]}"


def file_digest(path: Path) -> str:
    """The ``sha256:`` digest of a source file's bytes.

    Computed over the file rather than the parsed rows so that a change in
    formatting is visible as a change in provenance, which is the honest answer:
    the file on disk is what the researcher handed over.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def text_digest(payload: str) -> str:
    """The ``sha256:`` digest of generated text, for staged artifacts."""
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


__all__ = [
    "BACKEND_ENV_FOUND",
    "OBSERVATION_SPEC",
    "OUTCOME_SPEC",
    "PAN_SPEC",
    "PROVENANCE_REQUIRED_MESSAGE",
    "RECORD_SPECS",
    "WEATHER_SPEC",
    "FieldSpec",
    "ProvenanceCategory",
    "RecordSpec",
    "bind_backend_env",
    "canonical_row",
    "content_id",
    "data_source_for",
    "file_digest",
    "id_field",
    "normalize_column",
    "normalize_pan",
    "provenance_field",
    "require_measurement_provenance",
    "text_digest",
    "weather_source_for",
]
