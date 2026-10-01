"""Module 07 - building dataset rows that cannot see the future.

The grain of a row is one **pan observation at one instant**: the state of a pan
as it was known at that instant, plus everything that was measurable or
forecastable by then, plus a target read from a measurement taken strictly later.

That choice is what makes the leakage question answerable rather than arguable.
Every feature is an *as-of* lookup bounded by the anchor instant, and the target
is an *after* lookup bounded below by the anchor plus the configured horizon. The
builder enforces both by construction and then re-checks every row it produced,
so a defect in the selection logic surfaces as a refused build rather than as a
flattering score three modules later.

The lookups are ``bisect`` calls into per-pan timestamp arrays, so a build is
O(n log n) over the records it read rather than a query per row per column. The
array approach also makes the ordering assumption explicit: every bound is
expressed against the same ascending timeline, and "strictly before" versus "at
or before" is a choice of ``bisect_left`` or ``bisect_right`` that can be read at
the call site.

Three things this module refuses to do, each of which would otherwise be
unnoticed:

* **It will not borrow a measurement from another source.** An observation's
  ambient temperature and the weather record's are separate columns with separate
  nullability, because they come from different instruments. Averaging them, or
  filling one from the other, would put a number in the file that nobody took.
* **It will not count a forecast as rain.** ``recent_rainfall_mm`` sums observed
  records only, and the record count beside it says whether the window was
  covered by anything at all, so a ``0.0`` built on an empty window cannot be
  read as a measurement of dry weather.
* **It will not use a static property the operator had not registered yet.** A
  pan registered after the anchor contributes no area, which makes the physics
  columns uncomputable for that row rather than borrowing a later registration.
"""

from __future__ import annotations

import itertools
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.db.collections import (
    ACTUAL_OUTCOMES,
    PAN_OBSERVATIONS,
    RISK_DEFINITIONS,
    SALT_PANS,
    WEATHER_OBSERVATIONS,
)
from app.models.actual_outcome import ActualOutcome
from app.models.base import normalize_datetime
from app.models.dataset import (
    DatasetReport,
    ExcludedFieldRecord,
    LeakageAudit,
    LeakageViolation,
    MissingFeature,
    TargetSpec,
)
from app.models.enums import (
    ResearchDataSource,
    ResearchTarget,
    RiskVariableDerivation,
    TargetKind,
    WeatherSource,
)
from app.models.pan_observation import PanObservation
from app.models.risk_definition import (
    RiskDefinition,
    RiskEvaluation,
    RiskSourceVariable,
)
from app.models.salt_pan import SaltPan
from app.models.weather import WeatherObservation
from app.services.dataset_catalog import (
    ANCHOR_KEY,
    EXCLUDED_FUTURE_FIELDS,
    EXCLUDED_KEY_FIELDS,
    FEATURE_INDEX,
    ROW_KEY_NAMES,
    TARGET_INDEX,
    FeatureDefinition,
    TargetDefinition,
    season_for,
)
from app.services.dataset_versioning import row_id_for
from app.services.mass_balance import MassBalanceService, RainfallDilutionError
from app.services.risk_catalog import (
    RiskVariableDefinition,
    resolve_risk_variable,
)
from app.services.risk_evaluation import evaluate_definition

#: How many records to read from a collection per page. Matches the largest page
#: the repository accepts, so paging never has to argue with it.
PAGE_SIZE = 1000

#: The causal rule, written out once and reported with every dataset so a reader
#: never has to reconstruct it from the code.
LEAKAGE_POLICY = (
    "A row is anchored at one pan observation. Every feature is read from a "
    "record that existed at or before that instant, and the target is read from "
    "a measurement taken at or after the anchor plus the configured horizon. No "
    "post-anchor measurement, and no identifier, is ever a feature. Where the "
    "label is computed by a researcher's rule rather than measured, the target "
    "check is replaced by a per-variable check: every variable the rule read is "
    "compared against the moment that variable was knowable, because that is what "
    "the label is a function of."
)

#: The individual assertions the audit makes. Stored on the dataset so "we
#: checked" is a list a reader can disagree with.
LEAKAGE_CHECKS: tuple[str, ...] = (
    ("every feature record describes an instant at or before the anchor, including "
    "the previous observation and the sliding rainfall window"),
    ("a registered pan's static features require salt_pans.created_at at or before "
    "the anchor instant"),
    ("the physics prediction consumed only the forecast rainfall in force at the "
    "anchor, never recorded rainfall"),
    "the target was measured at or after the anchor plus the horizon",
    ("where the label is rule-derived instead, every variable the rule read is "
     "known at the anchor, unless the build acknowledged that its labels are "
     "retrospective"),
    ("a rule-derived label always records the definition, version and source "
     "variables it came from"),
    "row identifiers and free-text notes are excluded from the feature set",
)

# -- reasons a value is absent ------------------------------------------------

NO_PREVIOUS_OBSERVATION = (
    "No earlier observation exists for this pan, so no change could be measured."
)

NO_AS_OF_WEATHER = (
    "No observed weather record exists for this pan at or before the anchor "
    "instant. A forecast is not substituted for a measurement."
)

NO_FORECAST_IN_FORCE = (
    "No forecast had been issued for this pan at or before the anchor instant, so "
    "no forecast rainfall was available to predict from."
)

PAN_NOT_REGISTERED_YET = (
    "The pan had not been registered at the anchor instant, so its area is not a "
    "property of that moment."
)

PAN_NOT_REGISTERED_AT_ALL = "The pan is not registered, so it has no area."

PHYSICS_NEEDS_AREA = (
    "The physics prediction needs the pan area, and no area was available at the "
    "anchor instant."
)
PHYSICS_NEEDS_STATE = (
    "The physics prediction needs a starting salinity and depth on the anchor "
    "observation, and one or both were not measured."
)
PHYSICS_NEEDS_FORECAST = (
    "The physics prediction needs a forecast rainfall depth. The rainfall that "
    "actually fell is a future measurement and is not used, so with no forecast "
    "in force there is nothing to dilute by."
)
PHYSICS_UNCOMPUTABLE = (
    "The mass balance rejected the inputs ({reason}), so no dilution is predicted "
    "rather than a prediction made on a corrected value."
)

TARGET_NOT_YET_MEASURED = (
    "No {field} was measured at or after the anchor plus the horizon. The row is "
    "kept and reported as unlabelled rather than dropped, so the cost of "
    "unmeasured outcomes stays visible."
)

MISSING_RISK_FEATURE = (
    "The dataset was not built with the {column!r} column, so the rule could not "
    "read it. The build selects features explicitly and this variable was not among "
    "them; no value was substituted from another column."
)

MISSING_RISK_OPERAND = (
    "{variable!r} is a difference of two catalogued figures and one of them, "
    "{operand!r}, held no value on this row, so the difference was not computed. A "
    "difference taken against a missing operand would be a number no instrument "
    "produced."
)

NON_NUMERIC_RISK_OPERAND = (
    "{variable!r} is a difference of two numeric figures and an operand held a "
    "non-numeric value, so the arithmetic is meaningless and was not attempted."
)

TARGET_NEEDS_ANCHOR_SALINITY = (
    "A change target needs a salinity on the anchor observation as well as one "
    "after the horizon, and the anchor observation did not record it."
)

TARGET_NEEDS_EDGES = (
    "The banded target needs bin edges and the specification supplied none, so no "
    "class can be assigned."
)

UNMEASURED_ON_ANCHOR = (
    "The anchor observation did not record this figure. It is not filled from the "
    "previous reading, a typical value, or the weather record."
)

EMPTY_RAINFALL_WINDOW = (
    "No observed weather record falls in the lookback window, so this 0.0 is an "
    "absence of records rather than a measurement of dry weather. Read it "
    "alongside recent_rainfall_record_count."
)


class LeakageError(RuntimeError):
    """A build produced rows that break the causal ordering.

    Raised instead of storing the dataset. A leaked dataset is not a dataset with
    a warning attached: it is one that will produce a score nobody can defend, and
    the only safe response is to refuse it and say which row was wrong. The full
    audit rides on the exception so a caller can report every violation rather
    than the first.
    """

    def __init__(self, audit: LeakageAudit) -> None:
        self.audit = audit
        detail = "; ".join(
            f"row {violation.row_id}, {violation.field}: {violation.reason}"
            for violation in audit.violations[:5]
        )
        super().__init__(
            f"Refusing to build a dataset with {len(audit.violations)} causal "
            f"ordering violation(s). {detail}"
        )


class BuildSpecError(ValueError):
    """A build specification that cannot be honoured as written."""


@dataclass(frozen=True)
class BuildSpec:
    """Everything a build needs to know, and nothing it can decide for itself.

    Notably absent: any default target. A caller that has not named a target
    cannot build a dataset, which is the entire point of the configurable target
    system.
    """

    target: TargetSpec
    feature_names: tuple[str, ...]
    lookback_hours: float
    pan_ids: tuple[str, ...]
    start: datetime | None
    end: datetime | None
    include_unlabelled: bool = True
    max_source_records: int = 20_000
    #: The stored rule that will derive the labels, for a derived target. Required
    #: when the target is derived and refused when it is not, so a measured label
    #: can never be quietly attributed to a rule that did not produce it.
    risk_definition: RiskDefinition | None = None

    @property
    def horizon(self) -> timedelta:
        return timedelta(hours=self.target.horizon_hours)

    def as_hashed_specification(self) -> dict[str, Any]:
        """The part of a specification that its dataset's identity depends on.

        ``include_unlabelled`` is here because it changes which rows exist, and
        ``max_source_records`` because it changes how much of each collection was
        read. Both alter the data, so both belong in the hash. So does the risk
        definition: two datasets built from the same pans by two different rules
        carry different labels and must not share an identity.
        """
        return {
            "target": {
                "name": self.target.name.value,
                "label_column": self.target.label_column,
                "kind": self.target.kind.value,
                "horizon_hours": self.target.horizon_hours,
                "require_rain_event": self.target.require_rain_event,
                "bin_edges": self.target.bin_edges,
                "requires_risk_definition": self.target.requires_risk_definition,
                "risk_definition_id": self.target.risk_definition_id,
                "risk_definition_version": self.target.risk_definition_version,
                "risk_specification_hash": self.target.risk_specification_hash,
                "allow_retrospective_label": self.target.allow_retrospective_label,
            },
            "risk_definition": (
                self.risk_definition.specification_hash
                if self.risk_definition is not None
                else None
            ),
            "feature_names": list(self.feature_names),
            "lookback_hours": self.lookback_hours,
            "pan_ids": list(self.pan_ids),
            "window": {"start": self.start, "end": self.end},
            "include_unlabelled": self.include_unlabelled,
            "max_source_records": self.max_source_records,
        }


@dataclass
class DatasetRow:
    """One row: a state at an instant, and what was measured after it."""

    row_id: str
    pan_id: str
    anchor_timestamp: datetime
    anchor_observation_id: str
    features: dict[str, Any] = field(default_factory=dict)
    label: float | str | None = None
    label_column: str = ""
    is_labelled: bool = False
    target_measured_at: datetime | None = None
    target_source_id: str | None = None
    target_source_collection: str | None = None
    missing_features: list[MissingFeature] = field(default_factory=list)
    observation_data_source: str | None = None
    weather_source: str | None = None
    used_forecast_rainfall: bool = False
    used_physics_prediction: bool = False

    #: The whole argument behind this row's label: which rule and version derived
    #: it, and the value of every variable that rule read. None for a measured
    #: target, where ``label_column`` plus the target provenance columns already
    #: say everything there is to say.
    risk: RiskEvaluation | None = None

    #: The latest instant any feature was read from. Asserted to be at or before
    #: the anchor; it is the quantity the leakage audit checks.
    latest_feature_timestamp: datetime | None = None

    def note_missing(self, column: str, reason: str) -> None:
        """Record that a column is empty, and why.

        The reason is kept beside the empty cell rather than in its place, so a
        reader can tell a value nobody measured from a value that was zero.
        """
        self.missing_features.append(MissingFeature(field=column, reason=reason))

    def advance_feature_clock(self, instant: datetime | None) -> None:
        """Record that a feature was read from a record describing ``instant``."""
        if instant is None:
            return
        if self.latest_feature_timestamp is None or instant > self.latest_feature_timestamp:
            self.latest_feature_timestamp = instant

    def blank_unset_features(self, feature_names: Sequence[str]) -> None:
        """Give every selected feature a key, so a row has one shape.

        A feature absent from the mapping and a feature present with a null value
        are the same thing to a reader of the file, and this makes them the same
        thing to the hasher too.
        """
        for name in feature_names:
            self.features.setdefault(name, None)

    def as_canonical_row(self) -> dict[str, Any]:
        """The row as a plain mapping, for hashing.

        The only representation of a row's content, so the hash and the exported
        file cannot come to describe different data. The risk block is included
        whole: a derived label is a function of its rule and its source variables,
        so a row that hashed its label without them would hash as two different
        rows whenever the same class was reached from different readings.
        """
        return {
            "row_id": self.row_id,
            "pan_id": self.pan_id,
            ANCHOR_KEY: normalize_datetime(self.anchor_timestamp),
            "anchor_observation_id": self.anchor_observation_id,
            self.label_column: self.label,
            "is_labelled": self.is_labelled,
            "target_measured_at": self.target_measured_at,
            "target_source_id": self.target_source_id,
            "target_source_collection": self.target_source_collection,
            "features": dict(self.features),
            "missing_features": [
                {"field": entry.field, "reason": entry.reason}
                for entry in sorted(self.missing_features, key=lambda item: item.field)
            ],
            "risk": self.risk.model_dump(mode="json") if self.risk else None,
        }

    #: The provenance columns a derived label travels with in the exported file.
    #: Named here so the builder, the exporter and a reader of the CSV agree on
    #: them, and so adding one is a change in one place rather than three.
    RISK_PROVENANCE_COLUMNS: tuple[str, ...] = (
        "risk_definition_id",
        "risk_definition_version",
        "risk_specification_hash",
        "risk_label_basis",
        "risk_source_variables",
        "risk_matched_criteria",
        "risk_unlabelled_reason",
    )

    def as_flat_row(self, feature_names: Sequence[str]) -> dict[str, Any]:
        """The row in file order: keys, then features, then the label block."""
        flat: dict[str, Any] = {
            "row_id": self.row_id,
            "pan_id": self.pan_id,
            ANCHOR_KEY: normalize_datetime(self.anchor_timestamp),
            "target_measured_at": self.target_measured_at,
            "target_source_id": self.target_source_id,
        }
        for name in feature_names:
            flat[name] = self.features.get(name)
        flat[self.label_column] = self.label
        flat["is_labelled"] = self.is_labelled
        for column in self.RISK_PROVENANCE_COLUMNS:
            flat[column] = self._risk_provenance(column)
        flat["missing_features"] = "; ".join(
            f"{entry.field}: {entry.reason}" for entry in self.missing_features
        )
        return flat

    def _risk_provenance(self, column: str) -> str | None:
        """One provenance cell, for a row whose label a rule derived.

        Null for a measured target rather than an empty string, so a reader can
        tell "no rule was involved" from "a rule was involved and wrote nothing
        here".
        """
        if self.risk is None:
            return None
        return {
            "risk_definition_id": self.risk.definition_id,
            "risk_definition_version": self.risk.definition_version,
            "risk_specification_hash": self.risk.specification_hash,
            "risk_label_basis": self.risk.label_basis.value,
            "risk_source_variables": self.risk.source_variables_text(),
            "risk_matched_criteria": self.risk.matched_criteria_text(),
            "risk_unlabelled_reason": self.risk.unlabelled_reason,
        }[column]


@dataclass
class BuiltDataset:
    """The result of one build, before anything is stored."""

    spec: BuildSpec
    target: TargetDefinition
    features: tuple[FeatureDefinition, ...]
    rows: tuple[DatasetRow, ...]
    audit: LeakageAudit
    sources: dict[str, Any]
    truncated_collections: tuple[str, ...] = ()
    #: Filled in by the report step, which runs over the finished rows. Held here
    #: so the rows, the report and the hash all describe the same build.
    report: DatasetReport | None = None

    def canonical_rows(self) -> list[dict[str, Any]]:
        """Rows in canonical order, ready to hash.

        Sorted by pan, then anchor instant, then row id. The order is imposed here
        rather than inherited from a query, so the hash does not depend on a sort
        the database is free to change.
        """
        return [
            row.as_canonical_row()
            for row in sorted(
                self.rows, key=lambda item: (item.pan_id, item.anchor_timestamp, item.row_id)
            )
        ]


class _PanTimeline:
    """The sorted records of one pan, indexed for as-of and after lookups.

    Built once per pan and queried by binary search, so adding a feature costs a
    search rather than a round trip. Each list is ascending, which is what makes
    ``bisect_left`` mean "strictly before" and ``bisect_right`` mean "at or before"
    at the call sites.
    """

    def __init__(
        self,
        pan: SaltPan | None,
        observations: Sequence[PanObservation],
        weather: Sequence[WeatherObservation],
        outcomes: Sequence[ActualOutcome],
    ) -> None:
        self.pan = pan
        self.observations = sorted(observations, key=lambda item: item.timestamp)
        self.weather = sorted(weather, key=lambda item: item.timestamp)
        self.outcomes = sorted(outcomes, key=lambda item: item.timestamp)

        self.observed_weather = [item for item in self.weather if not item.is_forecast]
        self.observed_weather_times = [item.timestamp for item in self.observed_weather]
        self.forecasts = sorted(
            (item for item in self.weather if item.is_forecast),
            key=lambda item: item.timestamp,
        )
        self.forecast_times = [item.timestamp for item in self.forecasts]
        self.observation_times = [item.timestamp for item in self.observations]
        self.outcome_times = [item.timestamp for item in self.outcomes]

    def previous_observation(self, at: datetime) -> PanObservation | None:
        index = bisect_left(self.observation_times, at) - 1
        return self.observations[index] if index >= 0 else None

    def observed_weather_at(self, at: datetime) -> WeatherObservation | None:
        index = bisect_right(self.observed_weather_times, at) - 1
        return self.observed_weather[index] if index >= 0 else None

    def forecast_in_force(self, at: datetime) -> WeatherObservation | None:
        index = bisect_right(self.forecast_times, at) - 1
        return self.forecasts[index] if index >= 0 else None

    def observed_weather_between(
        self, after: datetime, until: datetime
    ) -> list[WeatherObservation]:
        """Observed weather in ``(after, until]``.

        Half-open on the left on purpose. A reading taken at the anchor instant is
        already the anchor's own weather, and counting it again inside the horizon
        would let a row be labelled by rain that was known before the prediction
        was made.
        """
        low = bisect_right(self.observed_weather_times, after)
        high = bisect_right(self.observed_weather_times, until)
        return self.observed_weather[low:high]

    def recent_observed_weather(
        self, at: datetime, lookback: timedelta
    ) -> list[WeatherObservation]:
        """Observed weather in ``[at - lookback, at]``."""
        low = bisect_left(self.observed_weather_times, at - lookback)
        high = bisect_right(self.observed_weather_times, at)
        return self.observed_weather[low:high]

    def first_observation_with(
        self, not_before: datetime, source_field: str
    ) -> PanObservation | None:
        """The first observation at or after an instant that measured a field.

        Scans forward past the readings that did not measure it rather than giving
        up: a visit that recorded only a temperature says nothing about the
        salinity at that instant, but it is not evidence that the salinity is
        unknowable at the next visit either.
        """
        index = bisect_left(self.observation_times, not_before)
        for observation in self.observations[index:]:
            if getattr(observation, source_field, None) is not None:
                return observation
        return None

    def first_outcome_with_harvest(self, not_before: datetime) -> ActualOutcome | None:
        """The first recorded outcome at or after an instant carrying a result."""
        index = bisect_left(self.outcome_times, not_before)
        for outcome in self.outcomes[index:]:
            if outcome.harvest_outcome is not None:
                return outcome
        return None


def band_label(value: float, edges: Sequence[float]) -> str:
    """Which band a measured value falls in.

    Bands are half-open upwards, so a value sitting exactly on an edge joins the
    band above it and every value lands in exactly one band. Stated because an
    off-by-one-edge would change a class distribution without changing a single
    row's features.
    """
    for lower, upper in itertools.pairwise(edges):
        if value < upper:
            return f"<{lower:g}" if value < lower else f"{lower:g}-{upper:g}"
    return f">={edges[-1]:g}"


def excluded_field_records() -> list[ExcludedFieldRecord]:
    """The documented exclusions, as records the manifest can carry.

    Public because the audit and the served catalogue must report the same list.
    Two renderings of "what was left out and why" would let a reader conclude
    the exclusions differ between the dataset's own audit and the documentation
    published beside it.
    """
    records = [
        ExcludedFieldRecord(
            field=field, reason=reason, reason_kind="future_measurement"
        )
        for field, reason in EXCLUDED_FUTURE_FIELDS
    ]
    records += [
        ExcludedFieldRecord(field=field, reason=reason, reason_kind="row_identifier")
        for field, reason in EXCLUDED_KEY_FIELDS
    ]
    return records


def _count_by(values: Sequence[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _window_of(timestamps: Sequence[datetime]) -> dict[str, Any]:
    if not timestamps:
        return {"first": None, "last": None, "count": 0}
    return {"first": min(timestamps), "last": max(timestamps), "count": len(timestamps)}


class DatasetBuilder:
    """Turns stored observations, weather and outcomes into dataset rows."""

    def __init__(self, mass_balance: MassBalanceService | None = None) -> None:
        self._mass_balance = mass_balance or MassBalanceService()

    # -- loading ------------------------------------------------------------

    @staticmethod
    async def _load(
        repository: Any,
        query: dict[str, Any],
        *,
        sort_field: str,
        cap: int,
    ) -> tuple[list[Any], bool]:
        """Read a collection in pages, stopping at the cap.

        Returns the records and whether the cap was reached. The flag is reported
        in the dataset rather than swallowed, because a dataset quietly built from
        the first N records is a sample and has to be described as one.
        """
        collected: list[Any] = []
        skip = 0
        while len(collected) <= cap:
            batch = await repository.find(
                query, sort=[(sort_field, 1)], skip=skip, limit=PAGE_SIZE
            )
            if not batch:
                break
            collected.extend(batch)
            skip += PAGE_SIZE
            if len(batch) < PAGE_SIZE:
                break
        truncated = len(collected) > cap
        return collected[:cap], truncated

    async def _load_timelines(
        self,
        *,
        spec: BuildSpec,
        observations: Any,
        weather: Any,
        outcomes: Any,
        pans: Any,
    ) -> tuple[
        dict[str, _PanTimeline],
        dict[str, int],
        dict[str, bool],
        dict[str, int],
    ]:
        """Read every record the window can reach and group it by pan.

        The window is widened by the lookback on the left and the horizon on the
        right, so a row anchored at the very edge of the caller's window still has
        its rainfall history and its outcome measurement available.

        Records marked as a demonstration are excluded here, at the query, rather
        than filtered out of the finished rows. Filtering afterwards would leave
        them having already shaped the loading cap and the truncation flag, so a
        dataset could report a sample of the research data while a demonstration
        record silently displaced a real one; excluding in the query means the cap
        is spent only on records that can become rows. The number withheld is
        returned and reported, because a dataset that quietly drops records is
        indistinguishable from one that never had them.
        """
        cap = spec.max_source_records
        window: dict[str, Any] = {}
        if spec.start is not None:
            window["$gte"] = spec.start - timedelta(hours=spec.lookback_hours)
        if spec.end is not None:
            window["$lte"] = spec.end + spec.horizon

        pan_filter: dict[str, Any] = (
            {"pan_id": {"$in": list(spec.pan_ids)}} if spec.pan_ids else {}
        )
        timed_filter = {**pan_filter, **({"timestamp": window} if window else {})}
        observation_filter = {
            **timed_filter,
            "data_source": {"$ne": ResearchDataSource.DEMONSTRATION.value},
        }
        weather_filter = {
            **timed_filter,
            "source": {"$ne": WeatherSource.DEMONSTRATION.value},
        }

        registered, pans_truncated = await self._load(
            pans, pan_filter, sort_field="pan_id", cap=cap
        )
        observation_records, observations_truncated = await self._load(
            observations, observation_filter, sort_field="timestamp", cap=cap
        )
        weather_records, weather_truncated = await self._load(
            weather, weather_filter, sort_field="timestamp", cap=cap
        )
        outcome_records, outcomes_truncated = await self._load(
            outcomes, timed_filter, sort_field="timestamp", cap=cap
        )

        excluded = {
            PAN_OBSERVATIONS: await observations.count(
                {
                    **timed_filter,
                    "data_source": ResearchDataSource.DEMONSTRATION.value,
                }
            ),
            WEATHER_OBSERVATIONS: await weather.count(
                {
                    **timed_filter,
                    "source": WeatherSource.DEMONSTRATION.value,
                }
            ),
        }

        pan_by_id = {pan.pan_id: pan for pan in registered}
        grouped: dict[str, dict[str, list[Any]]] = {
            pan_id: {"observations": [], "weather": [], "outcomes": []}
            for pan_id in pan_by_id
        }
        for collection, records in (
            ("observations", observation_records),
            ("weather", weather_records),
            ("outcomes", outcome_records),
        ):
            for record in records:
                bucket = grouped.get(record.pan_id)
                if bucket is not None:
                    bucket[collection].append(record)

        timelines = {
            pan_id: _PanTimeline(pan_by_id[pan_id], **bucket)
            for pan_id, bucket in grouped.items()
        }
        counts = {
            SALT_PANS: len(registered),
            PAN_OBSERVATIONS: len(observation_records),
            WEATHER_OBSERVATIONS: len(weather_records),
            ACTUAL_OUTCOMES: len(outcome_records),
        }
        truncated_flags = {
            SALT_PANS: pans_truncated,
            PAN_OBSERVATIONS: observations_truncated,
            WEATHER_OBSERVATIONS: weather_truncated,
            ACTUAL_OUTCOMES: outcomes_truncated,
        }
        return timelines, counts, truncated_flags, excluded

    # -- feature assembly ---------------------------------------------------

    @staticmethod
    def _observation_features(
        row: DatasetRow, anchor: PanObservation, timeline: _PanTimeline
    ) -> None:
        """Read the anchor observation and the change against its predecessor."""
        row.features["current_salinity_g_l"] = anchor.salinity_g_l
        row.features["water_depth_cm"] = anchor.water_depth_cm
        row.features["brine_temperature_c"] = anchor.brine_temperature_c
        row.features["observation_ambient_temperature_c"] = anchor.ambient_temperature_c
        row.features["observation_humidity_percent"] = anchor.humidity_percent
        for column, value in (
            ("current_salinity_g_l", anchor.salinity_g_l),
            ("water_depth_cm", anchor.water_depth_cm),
            ("brine_temperature_c", anchor.brine_temperature_c),
            ("observation_ambient_temperature_c", anchor.ambient_temperature_c),
            ("observation_humidity_percent", anchor.humidity_percent),
        ):
            if value is None:
                row.note_missing(column, UNMEASURED_ON_ANCHOR)

        previous = timeline.previous_observation(anchor.timestamp)
        if previous is None:
            row.note_missing("salinity_change_g_l", NO_PREVIOUS_OBSERVATION)
            row.note_missing("depth_change_cm", NO_PREVIOUS_OBSERVATION)
            row.note_missing("hours_since_previous_observation", NO_PREVIOUS_OBSERVATION)
            return

        row.advance_feature_clock(previous.timestamp)
        if anchor.salinity_g_l is None or previous.salinity_g_l is None:
            row.note_missing(
                "salinity_change_g_l",
                "The change needs a salinity on this observation and on the "
                "previous one, and one of them was not measured.",
            )
        else:
            row.features["salinity_change_g_l"] = anchor.salinity_g_l - previous.salinity_g_l
        if anchor.water_depth_cm is None or previous.water_depth_cm is None:
            row.note_missing(
                "depth_change_cm",
                "The change needs a depth on this observation and on the previous "
                "one, and one of them was not measured.",
            )
        else:
            row.features["depth_change_cm"] = (
                anchor.water_depth_cm - previous.water_depth_cm
            )
        row.features["hours_since_previous_observation"] = (
            anchor.timestamp - previous.timestamp
        ).total_seconds() / 3600.0

    @staticmethod
    def _time_features(row: DatasetRow) -> None:
        """Calendar columns derived from the anchor instant.

        Included because the solar cycle is a real driver of evaporation, not
        because a date is a convenient number. Both are functions of the anchor
        alone, so neither can carry information from after it.
        """
        anchor = row.anchor_timestamp
        row.features["hour_of_day_utc"] = anchor.hour
        row.features["day_of_year"] = anchor.timetuple().tm_yday
        row.features["season"] = season_for(anchor.month)

    @staticmethod
    def _weather_features(
        row: DatasetRow, timeline: _PanTimeline, lookback: timedelta
    ) -> None:
        """Read the weather known at the anchor: what fell, and what was forecast."""
        observed = timeline.observed_weather_at(row.anchor_timestamp)
        if observed is None:
            row.note_missing("weather_ambient_temperature_c", NO_AS_OF_WEATHER)
            row.note_missing("weather_humidity_percent", NO_AS_OF_WEATHER)
        else:
            row.advance_feature_clock(observed.timestamp)
            row.weather_source = str(observed.source)
            row.features["weather_ambient_temperature_c"] = observed.ambient_temperature_c
            row.features["weather_humidity_percent"] = observed.humidity_percent
            if observed.ambient_temperature_c is None:
                row.note_missing(
                    "weather_ambient_temperature_c",
                    "The nearest observed weather record at the anchor did not "
                    "carry an ambient temperature.",
                )
            if observed.humidity_percent is None:
                row.note_missing(
                    "weather_humidity_percent",
                    "The nearest observed weather record at the anchor did not "
                    "carry a humidity figure.",
                )

        forecast = timeline.forecast_in_force(row.anchor_timestamp)
        if forecast is None:
            row.note_missing("forecast_rainfall_mm", NO_FORECAST_IN_FORCE)
            row.note_missing(
                "forecast_rainfall_probability_percent", NO_FORECAST_IN_FORCE
            )
        else:
            row.advance_feature_clock(forecast.timestamp)
            row.features["forecast_rainfall_mm"] = forecast.rainfall_mm
            row.features["forecast_rainfall_probability_percent"] = (
                forecast.rainfall_probability_percent
            )
            if forecast.rainfall_mm is None:
                row.note_missing(
                    "forecast_rainfall_mm",
                    "A forecast had been issued by the anchor instant but carried "
                    "no rainfall depth. It is left empty rather than read as zero.",
                )
            else:
                row.used_forecast_rainfall = True
            if forecast.rainfall_probability_percent is None:
                row.note_missing(
                    "forecast_rainfall_probability_percent",
                    "The forecast in force at the anchor carried no rainfall "
                    "probability.",
                )

        window = timeline.recent_observed_weather(row.anchor_timestamp, lookback)
        row.features["recent_rainfall_record_count"] = len(window)
        total = 0.0
        for record in window:
            row.advance_feature_clock(record.timestamp)
            if record.rainfall_mm is not None:
                total += record.rainfall_mm
        row.features["recent_rainfall_mm"] = total
        if not window:
            row.note_missing("recent_rainfall_mm", EMPTY_RAINFALL_WINDOW)

    @staticmethod
    def _static_features(row: DatasetRow, timeline: _PanTimeline) -> None:
        pan = timeline.pan
        if pan is None:
            row.note_missing("pan_area_m2", PAN_NOT_REGISTERED_AT_ALL)
            return
        if pan.created_at > row.anchor_timestamp:
            row.note_missing("pan_area_m2", PAN_NOT_REGISTERED_YET)
            return
        row.features["pan_area_m2"] = pan.area_m2

    def _physics_features(
        self, row: DatasetRow, anchor: PanObservation, timeline: _PanTimeline
    ) -> None:
        """Run Module 06's balance on the state and the forecast known at the anchor.

        The rainfall input is the forecast in force, never the rainfall that fell.
        That single choice is what keeps the physics columns on the correct side of
        the anchor: the balance becomes exactly the prediction an operator could
        have made at that moment, which is what makes it a feature rather than an
        answer.
        """
        columns = (
            "physics_predicted_post_rain_salinity_g_l",
            "physics_predicted_dilution_percent",
            "physics_prediction_rainfall_source",
        )
        pan = timeline.pan
        forecast = timeline.forecast_in_force(row.anchor_timestamp)
        rainfall_mm = forecast.rainfall_mm if forecast is not None else None

        if pan is None or pan.created_at > row.anchor_timestamp:
            for column in columns:
                row.note_missing(column, PHYSICS_NEEDS_AREA)
            return
        row.advance_feature_clock(pan.created_at)

        if anchor.salinity_g_l is None or anchor.water_depth_cm is None:
            for column in columns:
                row.note_missing(column, PHYSICS_NEEDS_STATE)
            return

        if rainfall_mm is None:
            for column in columns:
                row.note_missing(column, PHYSICS_NEEDS_FORECAST)
            return

        try:
            dilution = self._mass_balance.evaluate(
                area_m2=pan.area_m2,
                initial_depth_m=anchor.water_depth_cm / 100.0,
                initial_salinity_g_l=anchor.salinity_g_l,
                rainfall_mm=rainfall_mm,
            )
        except RainfallDilutionError as exc:
            for column in columns:
                row.note_missing(column, PHYSICS_UNCOMPUTABLE.format(reason=str(exc)))
            return

        row.features["physics_predicted_post_rain_salinity_g_l"] = (
            dilution.post_rain_salinity_g_l
        )
        row.features["physics_prediction_rainfall_source"] = "forecast"
        row.used_physics_prediction = True
        if dilution.dilution_percent is None:
            row.note_missing(
                "physics_predicted_dilution_percent",
                dilution.dilution_percent_reason
                or "The dilution percentage is undefined for this row's inputs.",
            )
        else:
            row.features["physics_predicted_dilution_percent"] = dilution.dilution_percent

    # -- target assembly ----------------------------------------------------

    def _assign_derived_label(
        self,
        row: DatasetRow,
        anchor: PanObservation,
        timeline: _PanTimeline,
        spec: BuildSpec,
        definition: TargetDefinition,
    ) -> None:
        """Derive this row's label by applying the researcher's own rule to it.

        No measurement produced this label, so there is no target instant and no
        target source document to record. What is recorded instead is the entire
        argument: which definition and version decided, what every variable it
        read was worth on this row, which criteria fired, and why the row is
        unlabelled when it is. A row that carries a class with none of that is a
        row whose class cannot be defended, which is the whole reason the label
        is configurable in the first place.

        Every branch here either produces a class the rule itself asserts, or
        leaves the row unlabelled. There is no path that produces a class the
        rule did not name, because a default severity invented by this module
        would be indistinguishable in the output from one the researcher chose.
        """
        risk_definition = spec.risk_definition
        if risk_definition is None:
            raise BuildSpecError(
                f"target {definition.name.value} is derived from a researcher's "
                "rule, but the build carries no risk definition; the module "
                "supplies no severity vocabulary and no threshold of its own"
            )

        readings = self._risk_readings(row, timeline, spec)
        evaluation = evaluate_definition(risk_definition, readings)
        row.risk = evaluation
        row.target_source_collection = RISK_DEFINITIONS
        row.target_source_id = risk_definition.definition_id

        if not evaluation.is_labelled:
            row.note_missing(
                definition.label_column,
                evaluation.unlabelled_reason
                or "the rule matched nothing and assigns no fallback class",
            )
            return

        row.label = evaluation.label
        row.is_labelled = True
        # A derived label has no measurement instant, and none is invented: the
        # anchor is not a measurement of the label, and writing it into the
        # provenance columns would assert one. For a retrospective rule the
        # recorded outcome behind it is noted, because there the event really was
        # observed and its instant is worth keeping.
        if evaluation.uses_post_anchor_variable:
            outcome = timeline.first_outcome_with_harvest(row.anchor_timestamp + spec.horizon)
            if outcome is not None:
                row.target_measured_at = outcome.timestamp

    def _risk_readings(
        self,
        row: DatasetRow,
        timeline: _PanTimeline,
        spec: BuildSpec,
    ) -> dict[str, RiskSourceVariable]:
        """Read every variable the rule needs from this row, and record each one.

        A variable that cannot be resolved is still returned, with a null value and
        the catalogue's own reason, so the evaluation is complete: a rule short of
        an input then looks different from a rule that never consulted one, and
        the reason travels with the row instead of being lost in a builder log.

        A difference is left unevaluated unless *both* operands are real numbers.
        Subtracting from a missing operand would manufacture a figure no
        instrument produced, and the result would be a plausible salinity loss
        derived from nothing.
        """
        risk_definition = spec.risk_definition
        if risk_definition is None:
            return {}
        readings: dict[str, RiskSourceVariable] = {}
        for name in risk_definition.variables_used:
            catalogue_entry = resolve_risk_variable(name)
            value, reason = self._read_risk_value(
                catalogue_entry, row, timeline, spec
            )
            readings[name] = RiskSourceVariable(
                variable=name,
                value=value,
                unit=catalogue_entry.unit,
                leakage_class=catalogue_entry.leakage_class,
                reason=reason,
            )
        return readings

    def _read_risk_value(
        self,
        variable: RiskVariableDefinition,
        row: DatasetRow,
        timeline: _PanTimeline,
        spec: BuildSpec,
    ) -> tuple[float | str | None, str | None]:
        """Resolve one catalogued variable against one row."""
        if variable.derivation_kind is RiskVariableDerivation.DIRECT:
            column = variable.column
            if column not in spec.feature_names and column not in row.features:
                return None, MISSING_RISK_FEATURE.format(column=column)
            value = row.features.get(column)
            if value is None:
                return None, variable.nullable_reason
            return value, None

        if variable.derivation_kind is RiskVariableDerivation.PAN_FIELD:
            pan = timeline.pan
            if pan is None or pan.target_harvest_salinity_g_l is None:
                return None, variable.nullable_reason
            return float(pan.target_harvest_salinity_g_l), None

        if variable.derivation_kind is RiskVariableDerivation.RECORDED_OUTCOME:
            outcome = timeline.first_outcome_with_harvest(
                row.anchor_timestamp + spec.horizon
            )
            if outcome is None:
                return None, variable.nullable_reason
            return str(outcome.harvest_outcome), None

        # A difference of two catalogued figures, in the declared operand order.
        left_name, right_name = variable.operands
        left = self._read_risk_value(resolve_risk_variable(left_name), row, timeline, spec)
        right = self._read_risk_value(resolve_risk_variable(right_name), row, timeline, spec)
        if left[0] is None or right[0] is None:
            missing = left_name if left[0] is None else right_name
            return None, MISSING_RISK_OPERAND.format(
                variable=variable.name, operand=missing
            )
        if isinstance(left[0], str) or isinstance(right[0], str):
            return None, NON_NUMERIC_RISK_OPERAND.format(variable=variable.name)
        return float(left[0]) - float(right[0]), None

    def _assign_target(
        self, row: DatasetRow, anchor: PanObservation, timeline: _PanTimeline, spec: BuildSpec
    ) -> None:
        """Read the label from a measurement taken strictly after the anchor.

        Every branch either produces a label from two real figures or leaves the
        row unlabelled with a reason. There is no path through here that produces
        a merely plausible number, which is the property that makes a
        configurable target safe to expose to a researcher.
        """
        target = spec.target
        definition = TARGET_INDEX[target.name]
        not_before = row.anchor_timestamp + spec.horizon

        if definition.is_derived:
            self._assign_derived_label(row, anchor, timeline, spec, definition)
            return

        if definition.source_collection == ACTUAL_OUTCOMES:
            outcome = timeline.first_outcome_with_harvest(not_before)
            if outcome is None:
                row.note_missing(
                    definition.label_column,
                    TARGET_NOT_YET_MEASURED.format(field="a recorded harvest result"),
                )
                return
            row.label = str(outcome.harvest_outcome)
            row.is_labelled = True
            row.target_measured_at = outcome.timestamp
            row.target_source_id = outcome.outcome_id
            row.target_source_collection = ACTUAL_OUTCOMES
            return

        observation = timeline.first_observation_with(not_before, definition.source_field)
        if observation is None:
            row.note_missing(
                definition.label_column,
                TARGET_NOT_YET_MEASURED.format(field=definition.source_field),
            )
            return
        measured = getattr(observation, definition.source_field)
        row.target_measured_at = observation.timestamp
        row.target_source_id = observation.observation_id
        row.target_source_collection = PAN_OBSERVATIONS

        if target.name is ResearchTarget.SALINITY_CHANGE_G_L:
            if anchor.salinity_g_l is None:
                row.note_missing(definition.label_column, TARGET_NEEDS_ANCHOR_SALINITY)
                row.target_measured_at = None
                row.target_source_id = None
                return
            row.label = measured - anchor.salinity_g_l
            row.is_labelled = True
            return

        if target.kind is TargetKind.CLASSIFICATION:
            if not target.bin_edges:
                row.note_missing(definition.label_column, TARGET_NEEDS_EDGES)
                row.target_measured_at = None
                row.target_source_id = None
                return
            row.label = band_label(measured, target.bin_edges)
            row.is_labelled = True
            return

        row.label = measured
        row.is_labelled = True

    # -- the audit ----------------------------------------------------------

    @staticmethod
    def _audit_derived_label(
        row: DatasetRow, spec: BuildSpec
    ) -> list[LeakageViolation]:
        """Check a rule-derived label against the causal rule, by its inputs.

        A computed label has no measurement instant to compare against the
        horizon, so that check cannot be made and pretending otherwise would be
        the easy way out. What *can* be checked is the thing that actually
        determines the label: every variable the rule read, against the moment it
        was knowable.

        Two failures are possible and they are not the same. A prospective rule
        that read a post-anchor variable is leakage, and is refused outright. A
        retrospective rule that read one is a legitimate description of an event
        that has already happened, but only if the build said so, because a
        retrospective label that looks prospective will be quoted as a forecast.
        """
        if row.risk is None:
            if row.is_labelled:
                return [
                    LeakageViolation(
                        row_id=row.row_id,
                        field="risk_definition_id",
                        reason=(
                            "the row is labelled by a rule but records no rule, so "
                            "the label cannot be traced to anything"
                        ),
                    )
                ]
            return []

        violations: list[LeakageViolation] = []
        post_anchor = [
            variable.variable
            for variable in row.risk.source_variables
            if not variable.leakage_class.is_known_at_anchor
        ]
        if not post_anchor:
            return violations

        if not spec.target.allow_retrospective_label:
            return [
                LeakageViolation(
                    row_id=row.row_id,
                    field="risk_source_variables",
                    reason=(
                        "the rule read "
                        + ", ".join(post_anchor)
                        + ", which is measured after the anchor, and the build did "
                        "not acknowledge that its labels are retrospective. A model "
                        "fitted to such a label would be handed the answer it is "
                        "asked to predict."
                    ),
                )
            ]
        return violations

    def _audit(self, rows: Sequence[DatasetRow], spec: BuildSpec) -> LeakageAudit:
        """Re-check every built row against the causal rule.

        The selection logic above already prevents leakage, so this is a second,
        independent pass over the finished rows. It costs a comparison per row and
        it is the difference between "the builder is careful" and "the builder was
        checked".
        """
        violations: list[LeakageViolation] = []
        horizon = spec.horizon
        derived = TARGET_INDEX[spec.target.name].is_derived
        for row in rows:
            if (
                row.latest_feature_timestamp is not None
                and row.latest_feature_timestamp > row.anchor_timestamp
            ):
                violations.append(
                    LeakageViolation(
                        row_id=row.row_id,
                        field="feature_timestamp",
                        reason=(
                            f"a feature was read from a record describing "
                            f"{row.latest_feature_timestamp.isoformat()}, after the "
                            f"anchor {row.anchor_timestamp.isoformat()}"
                        ),
                    )
                )
            if derived:
                violations.extend(self._audit_derived_label(row, spec))
            elif row.is_labelled and (
                row.target_measured_at is None
                or row.target_measured_at < row.anchor_timestamp + horizon
            ):
                violations.append(
                    LeakageViolation(
                        row_id=row.row_id,
                        field="target_measured_at",
                        reason=(
                            "the label is not backed by a measurement at or after "
                            f"the anchor plus the {spec.target.horizon_hours} h horizon"
                        ),
                    )
                )
            source = row.features.get("physics_prediction_rainfall_source")
            if source is not None and source != "forecast":
                violations.append(
                    LeakageViolation(
                        row_id=row.row_id,
                        field="physics_prediction_rainfall_source",
                        reason=(
                            f"the physics prediction consumed {source!r} rather than "
                            "the forecast in force at the anchor"
                        ),
                    )
                )
        return LeakageAudit(
            policy=LEAKAGE_POLICY,
            rows_checked=len(rows),
            rows_passed=len(rows) - len(violations),
            violations=violations,
            excluded_fields=excluded_field_records(),
            checks=list(LEAKAGE_CHECKS),
        )

    # -- the build ----------------------------------------------------------

    async def build(
        self,
        *,
        spec: BuildSpec,
        observations: Any,
        weather: Any,
        outcomes: Any,
        pans: Any,
    ) -> BuiltDataset:
        """Build the rows, audit them, and return them with their provenance.

        Raises :class:`LeakageError` rather than returning a dataset whose rows
        fail the audit, and :class:`BuildSpecError` for a specification that
        cannot be honoured.
        """
        if spec.target.name not in TARGET_INDEX:
            raise BuildSpecError(f"unknown research target {spec.target.name.value!r}")
        if spec.start is not None and spec.end is not None and spec.start > spec.end:
            raise BuildSpecError("start must not be later than end")
        if spec.lookback_hours < 0:
            raise BuildSpecError("lookback_hours must not be negative")
        unknown = [name for name in spec.feature_names if name not in FEATURE_INDEX]
        if unknown:
            raise BuildSpecError("unknown feature name(s): " + ", ".join(sorted(unknown)))
        if not spec.feature_names:
            raise BuildSpecError(
                "a dataset must select at least one feature; an empty feature set "
                "cannot produce a row that means anything"
            )

        definition = TARGET_INDEX[spec.target.name]
        timelines, source_counts, truncated, excluded_demonstrations = (
            await self._load_timelines(
                spec=spec,
                observations=observations,
                weather=weather,
                outcomes=outcomes,
                pans=pans,
            )
        )

        features = tuple(FEATURE_INDEX[name] for name in spec.feature_names)
        lookback = timedelta(hours=spec.lookback_hours)
        rows: list[DatasetRow] = []

        for pan_id in sorted(timelines):
            timeline = timelines[pan_id]
            for anchor in timeline.observations:
                if spec.start is not None and anchor.timestamp < spec.start:
                    continue
                if spec.end is not None and anchor.timestamp > spec.end:
                    continue
                if spec.target.require_rain_event:
                    horizon_rain = timeline.observed_weather_between(
                        anchor.timestamp, anchor.timestamp + spec.horizon
                    )
                    if not any(
                        record.rainfall_mm is not None and record.rainfall_mm > 0
                        for record in horizon_rain
                    ):
                        continue

                row = DatasetRow(
                    row_id=row_id_for(pan_id, anchor.timestamp),
                    pan_id=pan_id,
                    anchor_timestamp=anchor.timestamp,
                    anchor_observation_id=anchor.observation_id,
                    label_column=spec.target.label_column,
                )
                row.observation_data_source = str(anchor.data_source)
                row.advance_feature_clock(anchor.timestamp)

                self._observation_features(row, anchor, timeline)
                self._time_features(row)
                self._weather_features(row, timeline, lookback)
                self._static_features(row, timeline)
                self._physics_features(row, anchor, timeline)
                self._assign_target(row, anchor, timeline, spec)
                row.blank_unset_features(spec.feature_names)

                if not row.is_labelled and not spec.include_unlabelled:
                    continue
                rows.append(row)

        audit = self._audit(rows, spec)
        if audit.violations:
            raise LeakageError(audit)

        timestamps_by_collection = {
            PAN_OBSERVATIONS: [
                record.timestamp
                for timeline in timelines.values()
                for record in timeline.observations
            ],
            WEATHER_OBSERVATIONS: [
                record.timestamp
                for timeline in timelines.values()
                for record in timeline.weather
            ],
            ACTUAL_OUTCOMES: [
                record.timestamp
                for timeline in timelines.values()
                for record in timeline.outcomes
            ],
        }
        sources = {
            "collection_counts": source_counts,
            "observation_sources": _count_by(
                [row.observation_data_source for row in rows if row.observation_data_source]
            ),
            "weather_sources": _count_by(
                [row.weather_source for row in rows if row.weather_source]
            ),
            "source_windows": {
                collection: _window_of(timestamps)
                for collection, timestamps in timestamps_by_collection.items()
            },
            "rows_using_forecast_rainfall": sum(
                1 for row in rows if row.used_forecast_rainfall
            ),
            "rows_using_physics_prediction": sum(
                1 for row in rows if row.used_physics_prediction
            ),
            "demonstration_records_excluded": dict(sorted(excluded_demonstrations.items())),
        }

        return BuiltDataset(
            spec=spec,
            target=definition,
            features=features,
            rows=tuple(rows),
            audit=audit,
            sources=sources,
            truncated_collections=tuple(
                collection
                for collection, was_truncated in sorted(truncated.items())
                if was_truncated
            ),
        )


__all__ = [
    "LEAKAGE_CHECKS",
    "LEAKAGE_POLICY",
    "PAGE_SIZE",
    "ROW_KEY_NAMES",
    "BuildSpec",
    "BuildSpecError",
    "BuiltDataset",
    "DatasetBuilder",
    "DatasetRow",
    "LeakageError",
    "band_label",
    "excluded_field_records",
]
