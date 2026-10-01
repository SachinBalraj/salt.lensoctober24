"""Module 07 - the catalogue of what a research dataset may contain.

This module is the single answer to two questions a reviewer asks before reading
a single row of a training file:

* **Where did this column come from?** Every column in :data:`FEATURE_CATALOG`
  names the collection and field it was read from, the rule used to pick that
  record, the unit, and the reason it can be null. A column that cannot explain
  where it came from does not belong in a research dataset.
* **Why is this not the answer leaking backwards?** Every column carries a
  :class:`~app.models.enums.LeakageClass`. ``as_of`` columns were measurable or
  forecastable at the row's anchor instant; ``static`` columns are registered
  pan properties no event changes; ``future`` columns are measurements taken
  after the anchor and are listed in :data:`EXCLUDED_FUTURE_FIELDS` purely so
  their absence is a documented decision rather than an oversight.

The target catalogue (:data:`TARGET_CATALOG`) is the configurable half. There is
no default target and no target that can be built without one being named
explicitly, because a dataset with an invented label teaches a model to predict
the label rather than the pan.

Three rules are enforced here rather than left to each call site.

**A feature is never a future measurement.** The builder selects every as-of
record with an explicit "at or before the anchor" bound, and the builder then
re-checks each row it produced. The two together are the reason the module can
claim causality rather than merely intending it.

**Identifiers are keys, not features.** ``pan_id`` and the anchor instant
identify a row; they are deliberately excluded from the feature set. A model
given the pan identifier can memorise one pan's outcomes and report a
flattering score on the same pans it trained on, which is leakage wearing a
different hat. The exclusion is recorded in :data:`EXCLUDED_FUTURE_FIELDS`
under its own heading so it is as visible as the leakage rules.

**Time features are included only where the physics justifies them.** Evaporation
in an open salt pan is driven by the solar cycle, so the hour of day and the
annual position carry real signal and are included with that reasoning stated.
A monsoon or a regional season boundary is *not* included: those are claims
about a specific climate zone, this project has no measured climatology to
support one, and guessing a boundary would be inventing domain knowledge. The
``season`` column that is included is a calendar band and says so.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.db.collections import (
    ACTUAL_OUTCOMES,
    PAN_OBSERVATIONS,
    RISK_DEFINITIONS,
    SALT_PANS,
    WEATHER_OBSERVATIONS,
)
from app.models.dataset import BUILDER_SCHEMA_VERSION
from app.models.enums import (
    FeatureKind,
    HarvestOutcome,
    LeakageClass,
    ResearchTarget,
    TargetKind,
)

#: The instant a row is anchored at. It is a key, not a feature: it defines the
#: moment every as-of lookup is bounded by.
ANCHOR_KEY = "anchor_timestamp"

#: How far ahead a target measurement must be for a row to be labelled. A
#: measurement taken at the same instant as the state it would predict
#: describes that state, not its consequence, so a target at or before the
#: anchor is refused rather than accepted as a trivially perfect label.
MIN_HORIZON_HOURS = 1.0

#: Default rainfall lookback for the ``recent_*`` columns, in hours. Three days
#: spans the gap between visits on a working salt pan and stays inside the
#: window in which a single reading set is a reasonable summary.
DEFAULT_LOOKBACK_HOURS = 72.0

#: How many rows a build may read from each source collection before it stops
#: and reports that the window was truncated. A bound, not a hint: an unbounded
#: scan over an append-only collection is how a research build silently becomes
#: a full-cluster copy.
DEFAULT_MAX_SOURCE_RECORDS = 20_000


@dataclass(frozen=True)
class FeatureDefinition:
    """One column of a dataset, and the argument for its existence.

    Frozen and data-only, so a dataset manifest can hold these verbatim and a
    reader can check the manifest against this catalogue without running code.
    """

    name: str
    kind: FeatureKind
    leakage_class: LeakageClass
    unit: str | None
    source_collection: str
    source_field: str
    selection_rule: str
    derivation: str
    scientific_basis: str
    nullable_reason: str | None = None


@dataclass(frozen=True)
class RowKeyDefinition:
    """A column that identifies a row without predicting anything."""

    name: str
    source: str
    description: str


@dataclass(frozen=True)
class TargetDefinition:
    """One quantity a dataset can be asked to learn, and where it is measured.

    ``label_column`` is the name the target takes in the built file. It is part
    of the specification and therefore part of the content hash, so renaming a
    target column produces a visibly different dataset rather than one that
    looks identical to an earlier experiment.
    """

    name: ResearchTarget
    kind: TargetKind
    label_column: str
    unit: str | None
    source_collection: str
    source_field: str
    description: str
    measurement_rule: str
    requires_bin_edges: bool = False
    classes: tuple[str, ...] | None = None
    #: Whether a build of this target must name a stored risk definition. True for
    #: the operational-risk target, whose label vocabulary and thresholds belong
    #: to a researcher rather than to this catalogue. A target that needs a rule
    #: cannot be built without one, because the alternative is a class
    #: distribution invented by the module.
    requires_risk_definition: bool = False

    @property
    def is_classification(self) -> bool:
        return self.kind is TargetKind.CLASSIFICATION

    @property
    def is_derived(self) -> bool:
        """Whether the label is computed from a rule rather than measured.

        A derived label cannot be audited by asking when the target was measured,
        because no measurement produced it. The audit substitutes a per-variable
        check against the rule's inputs, which is what
        :attr:`requires_risk_definition` exists to enable.
        """
        return self.requires_risk_definition


# -- the feature catalogue ----------------------------------------------------

#: Every column Module 07 can build, in the order it appears in a file.
#:
#: Order is part of the contract: the CSV column order, the hash payload and the
#: manifest all use it, so a feature added in the middle is a schema change
#: rather than a silent reshuffle.
FEATURE_CATALOG: tuple[FeatureDefinition, ...] = (
    FeatureDefinition(
        name="current_salinity_g_l",
        kind=FeatureKind.NUMERIC,
        leakage_class=LeakageClass.AS_OF,
        unit="g/L",
        source_collection=PAN_OBSERVATIONS,
        source_field="salinity_g_l",
        selection_rule="The pan observation the row is anchored at.",
        derivation="Read directly from the anchor observation.",
        scientific_basis=(
            "Brine concentration is the state variable the whole system turns on: "
            "dilution is a ratio against it and harvest decisions are thresholds "
            "on it."
        ),
        nullable_reason=(
            "The anchor observation did not record a salinity. An observation "
            "carries only the figures its operator actually measured."
        ),
    ),
    FeatureDefinition(
        name="water_depth_cm",
        kind=FeatureKind.NUMERIC,
        leakage_class=LeakageClass.AS_OF,
        unit="cm",
        source_collection=PAN_OBSERVATIONS,
        source_field="water_depth_cm",
        selection_rule="The pan observation the row is anchored at.",
        derivation="Read directly from the anchor observation.",
        scientific_basis=(
            "Depth sets the brine volume, so it is the second term of the mass "
            "balance and the denominator of any dilution percentage."
        ),
        nullable_reason="The anchor observation did not record a depth.",
    ),
    FeatureDefinition(
        name="brine_temperature_c",
        kind=FeatureKind.NUMERIC,
        leakage_class=LeakageClass.AS_OF,
        unit="degC",
        source_collection=PAN_OBSERVATIONS,
        source_field="brine_temperature_c",
        selection_rule="The pan observation the row is anchored at.",
        derivation="Read directly from the anchor observation.",
        scientific_basis=(
            "Temperature sets saturation: the same concentration is closer to "
            "crystallising in cold brine than in warm, so it conditions how much "
            "further evaporation can go before salt precipitates."
        ),
        nullable_reason="The anchor observation did not record a brine temperature.",
    ),
    FeatureDefinition(
        name="observation_ambient_temperature_c",
        kind=FeatureKind.NUMERIC,
        leakage_class=LeakageClass.AS_OF,
        unit="degC",
        source_collection=PAN_OBSERVATIONS,
        source_field="ambient_temperature_c",
        selection_rule="The pan observation the row is anchored at.",
        derivation="Read directly from the anchor observation.",
        scientific_basis=(
            "Air temperature drives evaporation, which is the only process that "
            "raises salinity in a pan between rain events."
        ),
        nullable_reason=(
            "The anchor observation did not record an ambient temperature. It is "
            "not filled from the weather record, which is a different instrument "
            "with a different provenance."
        ),
    ),
    FeatureDefinition(
        name="observation_humidity_percent",
        kind=FeatureKind.NUMERIC,
        leakage_class=LeakageClass.AS_OF,
        unit="percent",
        source_collection=PAN_OBSERVATIONS,
        source_field="humidity_percent",
        selection_rule="The pan observation the row is anchored at.",
        derivation="Read directly from the anchor observation.",
        scientific_basis=(
            "Humidity sets the vapour-pressure deficit, the gradient that moves "
            "water out of the brine."
        ),
        nullable_reason=(
            "The anchor observation did not record a humidity. It is not filled "
            "from the weather record."
        ),
    ),
    FeatureDefinition(
        name="weather_ambient_temperature_c",
        kind=FeatureKind.NUMERIC,
        leakage_class=LeakageClass.AS_OF,
        unit="degC",
        source_collection=WEATHER_OBSERVATIONS,
        source_field="ambient_temperature_c",
        selection_rule=(
            "The newest weather record for the pan with is_forecast = false and "
            "timestamp at or before the anchor instant."
        ),
        derivation=(
            "Read from the as-of observed weather record. Kept separate from the "
            "observation's own ambient temperature rather than merged with it: "
            "the two come from different instruments and averaging them would "
            "invent a value that was never measured."
        ),
        scientific_basis=(
            "The nearest weather reading is a denser sample of the same forcing "
            "than the pan visits alone provide."
        ),
        nullable_reason=(
            "No observed weather record exists for this pan at or before the "
            "anchor instant. Absence is left as absence; a forecast is not "
            "substituted for a measurement."
        ),
    ),
    FeatureDefinition(
        name="weather_humidity_percent",
        kind=FeatureKind.NUMERIC,
        leakage_class=LeakageClass.AS_OF,
        unit="percent",
        source_collection=WEATHER_OBSERVATIONS,
        source_field="humidity_percent",
        selection_rule=(
            "The newest observed weather record for the pan at or before the "
            "anchor instant, as for the temperature column above."
        ),
        derivation="Read from the as-of observed weather record.",
        scientific_basis="Humidity governs evaporative loss between visits.",
        nullable_reason=(
            "No observed weather record exists for this pan at or before the "
            "anchor instant."
        ),
    ),
    FeatureDefinition(
        name="forecast_rainfall_mm",
        kind=FeatureKind.NUMERIC,
        leakage_class=LeakageClass.AS_OF,
        unit="mm",
        source_collection=WEATHER_OBSERVATIONS,
        source_field="rainfall_mm",
        selection_rule=(
            "The newest forecast for the pan with is_forecast = true and "
            "timestamp at or before the anchor instant, selected by issue time "
            "rather than by the instant it predicts."
        ),
        derivation=(
            "Read from the forecast that had been issued by the anchor instant. "
            "Selecting by issue time is the causal test: a forecast retrieved "
            "after the anchor, however well it happens to describe the rain, was "
            "not available to anybody at the moment being predicted."
        ),
        scientific_basis=(
            "Rainfall is the only input that dilutes brine, and a forecast of it "
            "issued before the rain is the decision-relevant quantity."
        ),
        nullable_reason=(
            "No forecast had been issued for this pan at or before the anchor "
            "instant, or the forecast in force carried no rainfall figure. The "
            "rainfall that actually fell is never used here; it is measured after "
            "the anchor and would be the answer."
        ),
    ),
    FeatureDefinition(
        name="forecast_rainfall_probability_percent",
        kind=FeatureKind.NUMERIC,
        leakage_class=LeakageClass.AS_OF,
        unit="percent",
        source_collection=WEATHER_OBSERVATIONS,
        source_field="rainfall_probability_percent",
        selection_rule="The same forecast record as forecast_rainfall_mm.",
        derivation="Read from the as-of forecast record.",
        scientific_basis=(
            "Probability and depth are different forecasts: a confident light "
            "rain and an uncertain heavy one call for different operator actions."
        ),
        nullable_reason=(
            "No forecast was in force at the anchor, or it carried no "
            "probability figure."
        ),
    ),
    FeatureDefinition(
        name="recent_rainfall_mm",
        kind=FeatureKind.NUMERIC,
        leakage_class=LeakageClass.AS_OF,
        unit="mm",
        source_collection=WEATHER_OBSERVATIONS,
        source_field="rainfall_mm",
        selection_rule=(
            "The sum of rainfall_mm over observed weather records for the pan "
            "with timestamp in the lookback window ending at the anchor instant."
        ),
        derivation=(
            "Sum of recorded observed rainfall, nothing else. Forecast rows are "
            "excluded so a prediction is not counted as rain. Zero means no "
            "rainfall was recorded in the window; whether the window was "
            "actually covered by any record is the separate "
            "recent_rainfall_record_count column, so a 0.0 built on an empty "
            "window cannot be read as a measurement of dry weather."
        ),
        scientific_basis=(
            "Recent rain is still in the pan as dilution, so the brine "
            "concentration at the anchor partly reflects the rain of the last "
            "few days rather than the current state alone."
        ),
        nullable_reason="The value is 0.0 when the window held no rainfall rows.",
    ),
    FeatureDefinition(
        name="recent_rainfall_record_count",
        kind=FeatureKind.NUMERIC,
        leakage_class=LeakageClass.AS_OF,
        unit="count",
        source_collection=WEATHER_OBSERVATIONS,
        source_field="_id",
        selection_rule=(
            "The number of observed weather records in the same lookback window "
            "as recent_rainfall_mm."
        ),
        derivation="Count of the records summed above.",
        scientific_basis=(
            "Coverage, not physics: it is what makes recent_rainfall_mm "
            "interpretable, because a zero supported by six records means "
            "something different from a zero supported by none."
        ),
        nullable_reason="The value is 0 when no observed weather record falls in the window.",
    ),
    FeatureDefinition(
        name="salinity_change_g_l",
        kind=FeatureKind.NUMERIC,
        leakage_class=LeakageClass.AS_OF,
        unit="g/L",
        source_collection=PAN_OBSERVATIONS,
        source_field="salinity_g_l",
        selection_rule=(
            "Anchor salinity minus the salinity on the newest pan observation "
            "strictly before the anchor instant."
        ),
        derivation=(
            "current_salinity_g_l - previous_observation.salinity_g_l. Null "
            "rather than 0.0 when either figure is missing, because no change "
            "was measured and reporting no change would be a fabrication."
        ),
        scientific_basis=(
            "The recent rate of concentration is the observable signature of "
            "evaporation versus dilution, and separates the two regimes that a "
            "single salinity reading cannot."
        ),
        nullable_reason=(
            "There is no earlier observation for this pan, or it did not record "
            "a salinity, or the anchor observation did not."
        ),
    ),
    FeatureDefinition(
        name="depth_change_cm",
        kind=FeatureKind.NUMERIC,
        leakage_class=LeakageClass.AS_OF,
        unit="cm",
        source_collection=PAN_OBSERVATIONS,
        source_field="water_depth_cm",
        selection_rule=(
            "Anchor depth minus the depth on the newest pan observation strictly "
            "before the anchor instant."
        ),
        derivation="water_depth_cm - previous_observation.water_depth_cm.",
        scientific_basis=(
            "A rising depth between visits is rain or transfer in; a falling one "
            "is evaporation or draw-off. The sign is the evidence."
        ),
        nullable_reason=(
            "There is no earlier observation for this pan, or it did not record "
            "a depth, or the anchor observation did not."
        ),
    ),
    FeatureDefinition(
        name="hours_since_previous_observation",
        kind=FeatureKind.NUMERIC,
        leakage_class=LeakageClass.AS_OF,
        unit="hours",
        source_collection=PAN_OBSERVATIONS,
        source_field="timestamp",
        selection_rule=(
            "Anchor instant minus the timestamp of the newest observation "
            "strictly before it."
        ),
        derivation=(
            "(anchor - previous).total_seconds() / 3600. Present so the two "
            "change columns above can be read as a rate: a 2 g/L rise over two "
            "hours and a 2 g/L rise over two weeks are different facts."
        ),
        scientific_basis=(
            "Change per unit time is the physically meaningful quantity for any "
            "process driven at a rate."
        ),
        nullable_reason="There is no earlier observation for this pan.",
    ),
    FeatureDefinition(
        name="pan_area_m2",
        kind=FeatureKind.NUMERIC,
        leakage_class=LeakageClass.STATIC,
        unit="m2",
        source_collection=SALT_PANS,
        source_field="area_m2",
        selection_rule=(
            "The registered pan's area, and only where the pan was registered at "
            "or before the anchor instant."
        ),
        derivation=(
            "Read from the pan's registration record. A pan registered after the "
            "anchor contributes nothing, because its registration was not part of "
            "the information available then."
        ),
        scientific_basis=(
            "Area converts a depth into a volume, so it is what makes the mass "
            "balance and therefore the physics columns computable at all."
        ),
        nullable_reason=(
            "The pan had not been registered at the anchor instant, so its area "
            "is not a feature of that moment."
        ),
    ),
    FeatureDefinition(
        name="physics_predicted_post_rain_salinity_g_l",
        kind=FeatureKind.NUMERIC,
        leakage_class=LeakageClass.AS_OF,
        unit="g/L",
        source_collection=WEATHER_OBSERVATIONS,
        source_field="rainfall_mm",
        selection_rule=(
            "Module 06's mass balance, run on the anchor salinity, the anchor "
            "depth and the forecast rainfall already in force at the anchor "
            "instant."
        ),
        derivation=(
            "C1 = C0 * V0 / (V0 + Vr) with V0 = area * depth and "
            "Vr = area * forecast_rainfall. The forecast rainfall is used rather "
            "than the rainfall that actually fell precisely because the actual "
            "rainfall is a future measurement; this is a prediction the operator "
            "could have made at the anchor, which is what makes it a feature."
        ),
        scientific_basis=(
            "A transparent, hand-checkable baseline. It gives a fitted model an "
            "honest competitor rather than forcing it to rediscover dilution "
            "from scratch, and it makes the mass balance's assumptions testable "
            "against data."
        ),
        nullable_reason=(
            "The balance needs a pan area, a starting depth, a starting "
            "salinity and a forecast rainfall depth. Any one of them missing "
            "leaves the prediction uncomputable, and it is left null rather than "
            "run on an assumed input."
        ),
    ),
    FeatureDefinition(
        name="physics_predicted_dilution_percent",
        kind=FeatureKind.NUMERIC,
        leakage_class=LeakageClass.AS_OF,
        unit="percent",
        source_collection=WEATHER_OBSERVATIONS,
        source_field="rainfall_mm",
        selection_rule="The same balance run as the column above.",
        derivation=(
            "((C0 - C1) / C0) * 100 from the same balance, where C1 is the "
            "predicted post-rain salinity."
        ),
        scientific_basis=(
            "The operator-facing quantity: a percentage of the brine that was "
            "diluted away is legible to a salt-pan operator in a way that g/L is "
            "not."
        ),
        nullable_reason=(
            "The same missing inputs as the predicted salinity, and additionally "
            "undefined when the starting salinity is exactly 0 g/L, because the "
            "percentage is a ratio against a figure that is then not there."
        ),
    ),
    FeatureDefinition(
        name="physics_prediction_rainfall_source",
        kind=FeatureKind.CATEGORICAL,
        leakage_class=LeakageClass.AS_OF,
        unit=None,
        source_collection=WEATHER_OBSERVATIONS,
        source_field="source",
        selection_rule="The provenance of the rainfall figure the balance consumed.",
        derivation=(
            "The literal string 'forecast', or null. A single-valued column, and "
            "that is the point: it exists so a reader can confirm from the data "
            "itself that no row was built from measured rainfall."
        ),
        scientific_basis=(
            "Provenance as a column rather than a footnote, so an accidental "
            "switch to actual rainfall would change the file's values and be "
            "visible in the class and distribution report."
        ),
        nullable_reason="The physics columns are uncomputable, so no rainfall figure was consumed.",
    ),
    FeatureDefinition(
        name="hour_of_day_utc",
        kind=FeatureKind.NUMERIC,
        leakage_class=LeakageClass.AS_OF,
        unit="hour",
        source_collection=PAN_OBSERVATIONS,
        source_field="timestamp",
        selection_rule="The UTC hour of the anchor instant, 0 to 23.",
        derivation="anchor_timestamp.hour, in UTC because the stored record is.",
        scientific_basis=(
            "Evaporation in an open pan is driven by insolation and peaks in the "
            "afternoon, so the diurnal phase of a reading conditions what its "
            "salinity means."
        ),
        nullable_reason="Never null; the anchor instant always exists.",
    ),
    FeatureDefinition(
        name="day_of_year",
        kind=FeatureKind.NUMERIC,
        leakage_class=LeakageClass.AS_OF,
        unit="day",
        source_collection=PAN_OBSERVATIONS,
        source_field="timestamp",
        selection_rule="The ordinal day of the anchor instant, 1 to 366.",
        derivation="anchor_timestamp.timetuple().tm_yday.",
        scientific_basis=(
            "Annual position sets solar input and, with it, the evaporative "
            "demand that separates a wet season from a dry one."
        ),
        nullable_reason="Never null; the anchor instant always exists.",
    ),
    FeatureDefinition(
        name="season",
        kind=FeatureKind.CATEGORICAL,
        leakage_class=LeakageClass.AS_OF,
        unit=None,
        source_collection=PAN_OBSERVATIONS,
        source_field="timestamp",
        selection_rule=(
            "A calendar band derived from day_of_year: Dec-Feb, Mar-May, Jun-Aug "
            "or Sep-Nov."
        ),
        derivation=(
            "Meteorological calendar quarters of the anchor instant. This is a "
            "calendar convention and nothing more: it is not a claim about the "
            "climate of any particular pan, and no regional season boundary is "
            "applied because this project holds no measured climatology that "
            "could support one."
        ),
        scientific_basis=(
            "A coarse, human-readable grouping of the annual cycle, useful for "
            "reporting a model that performs differently across the year without "
            "asserting a climate regime."
        ),
        nullable_reason="Never null; the anchor instant always exists.",
    ),
)

#: Calendar bands for the ``season`` column, by the months each one covers.
#: Kept as data so the definition is inspectable rather than buried in a
#: conditional.
SEASON_BANDS: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("winter", (12, 1, 2)),
    ("spring", (3, 4, 5)),
    ("summer", (6, 7, 8)),
    ("autumn", (9, 10, 11)),
)

_MONTH_TO_SEASON: dict[int, str] = {
    month: label for label, months in SEASON_BANDS for month in months
}


def season_for(month: int) -> str:
    """The calendar band a month falls in."""
    try:
        return _MONTH_TO_SEASON[month]
    except KeyError as exc:
        raise ValueError(f"no calendar band is defined for month {month}") from exc


#: Row identity columns. Exported as part of the file so the feature set and the
#: key set can never be confused for one another.
ROW_KEYS: tuple[RowKeyDefinition, ...] = (
    RowKeyDefinition(
        name="row_id",
        source="derived",
        description=(
            "Stable identifier for this row, derived from the pan and the anchor "
            "instant so the same build produces the same identifiers."
        ),
    ),
    RowKeyDefinition(
        name="pan_id",
        source=SALT_PANS,
        description=(
            "The pan the row describes. Deliberately not a feature: an identifier "
            "that is in the training set lets a model memorise one pan's "
            "behaviour and then be scored on the same pans."
        ),
    ),
    RowKeyDefinition(
        name=ANCHOR_KEY,
        source=PAN_OBSERVATIONS,
        description=(
            "The instant the row describes. Every as-of feature is bounded by "
            "it, and the target is measured strictly after it."
        ),
    ),
    RowKeyDefinition(
        name="target_measured_at",
        source="derived",
        description=(
            "When the target was measured. Null on an unlabelled row, and never "
            "earlier than the anchor plus the configured horizon."
        ),
    ),
    RowKeyDefinition(
        name="target_source_id",
        source="derived",
        description=(
            "Identifier of the record the target was measured from, so a figure "
            "in a training file can be traced back to the observation that "
            "produced it."
        ),
    ),
    RowKeyDefinition(
        name="is_labelled",
        source="derived",
        description=(
            "Whether this row carries a target. False rows are built and reported "
            "so the cost of unmeasured outcomes is visible instead of silently "
            "dropped."
        ),
    ),
)

#: Columns written for every row but holding no modelled value.
LABEL_COLUMNS: tuple[str, ...] = ("label", "is_labelled")

#: Stored so a row can always say why a column is empty.
MISSING_REASON_COLUMNS: tuple[str, ...] = ("missing_features",)


#: Fields that exist in the source records and are deliberately never built as
#: features, with the reason for each. Reported by the leakage audit so the
#: exclusions are part of the dataset's documentation rather than a gap in it.
EXCLUDED_FUTURE_FIELDS: tuple[tuple[str, str], ...] = (
    (
        f"{ACTUAL_OUTCOMES}.actual_rainfall_mm",
        ("The rainfall that actually fell is measured after the anchor instant. "
        "Using it would hand the model the answer to 'did it rain', which is the "
        "event whose effect on salinity is being predicted."),
    ),
    (
        f"{ACTUAL_OUTCOMES}.forecast_rainfall_mm",
        ("The forecast rainfall as it was typed onto the outcome, which is filed "
        "when the event is graded. The forecast in force at the anchor is used "
        "instead, selected by issue time."),
    ),
    (
        f"{ACTUAL_OUTCOMES}.post_event_salinity_g_l",
        ("A post-event measurement. It is the salinity target, and using it as a "
        "predictor of itself is the most direct leakage there is."),
    ),
    (
        f"{ACTUAL_OUTCOMES}.post_event_depth_cm",
        ("A post-event measurement of the same quantity the depth target is drawn "
        "from."),
    ),
    (
        f"{ACTUAL_OUTCOMES}.operator_action",
        ("Written by an operator who was reacting to the event. It describes the "
        "consequence, not the state that preceded it."),
    ),
    (
        f"{ACTUAL_OUTCOMES}.harvest_outcome",
        ("The outcome of the event. It is the harvest target and cannot also "
        "explain itself."),
    ),
    (
        f"{ACTUAL_OUTCOMES}.simulation_error",
        ("A comparison against a post-event measurement, so it carries the answer "
        "in its construction."),
    ),
    (
        f"{PAN_OBSERVATIONS}.* after the anchor instant",
        ("Every later reading for the pan, including backfilled ones. A reading "
        "taken after the rain is not the state the rain fell on."),
    ),
    (
        f"{WEATHER_OBSERVATIONS}.* with timestamp after the anchor instant",
        ("Measured or issued after the anchor. The rainfall actually recorded in "
        "the target window is excluded for exactly this reason."),
    ),
)

#: Non-measurement columns excluded for a different reason. Grouped separately
#: because leaking an identifier is a subtler failure than leaking a future
#: measurement, and the two deserve different sentences.
EXCLUDED_KEY_FIELDS: tuple[tuple[str, str], ...] = (
    (
        f"{SALT_PANS}.pan_id",
        ("A row identifier. Included as a feature it lets the model memorise per-pan "
        "outcomes, and a random split puts the same pan in train and test, so the "
        "score measures memory rather than physics."),
    ),
    (
        f"{SALT_PANS}.pan_name / location_name / latitude / longitude",
        ("Pan identity and location, for the same reason as pan_id. They are "
        "carried in the manifest as context, not as predictors."),
    ),
    (
        f"{SALT_PANS}.risk_thresholds",
        ("Operator-configured limits rather than measurements. They are targets of "
        "operator judgement, so a model given them learns the operator's policy."),
    ),
    (
        f"{PAN_OBSERVATIONS}.note",
        ("Free text written by an operator. It routinely describes what the pan did "
        "next, and no automated reading of it can be guaranteed free of that."),
    ),
)


# -- the target catalogue -----------------------------------------------------

#: Every target a dataset can be built for. A build must name one of these; the
#: module has no default, because a dataset whose label was chosen for the
#: researcher is a dataset that will report whatever the researcher hoped for.
TARGET_CATALOG: tuple[TargetDefinition, ...] = (
    TargetDefinition(
        name=ResearchTarget.POST_RAIN_SALINITY_G_L,
        kind=TargetKind.REGRESSION,
        label_column="label_post_rain_salinity_g_l",
        unit="g/L",
        source_collection=PAN_OBSERVATIONS,
        source_field="salinity_g_l",
        description="Brine salinity as measured after the horizon elapses.",
        measurement_rule=(
            "The salinity on the first pan observation at or after the anchor "
            "plus the configured horizon that actually recorded a salinity."
        ),
    ),
    TargetDefinition(
        name=ResearchTarget.POST_RAIN_DEPTH_CM,
        kind=TargetKind.REGRESSION,
        label_column="label_post_rain_depth_cm",
        unit="cm",
        source_collection=PAN_OBSERVATIONS,
        source_field="water_depth_cm",
        description="Water depth as measured after the horizon elapses.",
        measurement_rule=(
            "The depth on the first pan observation at or after the anchor plus "
            "the configured horizon that actually recorded a depth."
        ),
    ),
    TargetDefinition(
        name=ResearchTarget.SALINITY_CHANGE_G_L,
        kind=TargetKind.REGRESSION,
        label_column="label_salinity_change_g_l",
        unit="g/L",
        source_collection=PAN_OBSERVATIONS,
        source_field="salinity_g_l",
        description=(
            "How much the brine concentrated or diluted between the anchor and "
            "the measurement after the horizon."
        ),
        measurement_rule=(
            "The salinity measured after the horizon minus the anchor salinity. "
            "Both operands must be real measurements; a change computed from an "
            "assumed starting figure is not built."
        ),
    ),
    TargetDefinition(
        name=ResearchTarget.POST_RAIN_SALINITY_BAND,
        kind=TargetKind.CLASSIFICATION,
        label_column="label_post_rain_salinity_band",
        unit=None,
        source_collection=PAN_OBSERVATIONS,
        source_field="salinity_g_l",
        description=(
            "Which salinity band the brine falls into after the horizon elapses, "
            "for a decision framed as a choice rather than a number."
        ),
        measurement_rule=(
            "The measured post-horizon salinity, banded by edges the caller "
            "supplies. The edges are required: choosing band boundaries here "
            "would mean the module decided where the classes are, and the class "
            "distribution would then be a fact about that decision."
        ),
        requires_bin_edges=True,
    ),
    TargetDefinition(
        name=ResearchTarget.HARVEST_OUTCOME,
        kind=TargetKind.CLASSIFICATION,
        label_column="label_harvest_outcome",
        unit=None,
        source_collection=ACTUAL_OUTCOMES,
        source_field="harvest_outcome",
        description=(
            "What came of the harvest after the event, from the closed "
            "vocabulary operators already record."
        ),
        measurement_rule=(
            "The harvest_outcome on the first recorded outcome for the pan at or "
            "after the anchor plus the horizon. A recorded 'none' is a label; a "
            "row whose outcome was never recorded is unlabelled."
        ),
        classes=tuple(member.value for member in HarvestOutcome),
    ),
    TargetDefinition(
        name=ResearchTarget.OPERATIONAL_RISK,
        kind=TargetKind.CLASSIFICATION,
        label_column="label_operational_risk",
        unit=None,
        # There is no collection and no field, because no measurement produces
        # this target. It is computed from a rule the researcher wrote, and these
        # two are recorded as the derivation's own inputs rather than left blank:
        # a blank pair would read as "this target has no provenance", which is the
        # opposite of the property it is being included for.
        source_collection=RISK_DEFINITIONS,
        source_field="specification_hash",
        description=(
            "An operational risk class derived by applying a researcher's own "
            "versioned rule to the measurable quantities available at the anchor. "
            "The class names, the thresholds and the severity ordering are all the "
            "researcher's; this catalogue supplies none of them."
        ),
        measurement_rule=(
            "The label is computed, not measured, so there is no measurement instant "
            "to check. Each criterion is applied to the catalogued value of its "
            "variable on this row, the matched criteria are resolved by the "
            "definition's own combinator, and the definition id, version and "
            "specification hash are recorded on the row. A row the rule cannot "
            "label is left unlabelled with a reason rather than assigned a class "
            "the rule does not assert. Because a computed label has no measurement "
            "to audit against the horizon, the causal audit instead checks every "
            "variable the rule read against that variable's leakage class, and a "
            "rule reading a post-anchor measurement is refused unless the build "
            "explicitly acknowledges that its labels are retrospective."
        ),
        requires_risk_definition=True,
    ),
)

FEATURE_INDEX: dict[str, FeatureDefinition] = {
    feature.name: feature for feature in FEATURE_CATALOG
}
TARGET_INDEX: dict[ResearchTarget, TargetDefinition] = {
    target.name: target for target in TARGET_CATALOG
}
ROW_KEY_NAMES: tuple[str, ...] = tuple(key.name for key in ROW_KEYS)


def feature_names() -> tuple[str, ...]:
    """Every feature name, in catalog order."""
    return tuple(feature.name for feature in FEATURE_CATALOG)


def feature_kinds(names: Sequence[str]) -> dict[str, FeatureKind]:
    """Map each requested feature to the kind declared in the catalogue.

    The kind is read from the catalogue and never inferred from the data. A
    column's type is a property of what it measures, and a column that happens to
    hold only integers this month is still the column it was defined to be.
    """
    kinds: dict[str, FeatureKind] = {}
    for name in names:
        definition = FEATURE_INDEX.get(name)
        if definition is None:
            raise LookupError(
                f"unknown feature {name!r}; it is not in the feature catalogue, "
                "so nothing can say how it should be encoded. Available features: "
                + ", ".join(feature.name for feature in FEATURE_CATALOG)
            )
        if definition.kind not in (FeatureKind.NUMERIC, FeatureKind.CATEGORICAL):
            raise LookupError(
                f"feature {name!r} has kind {definition.kind!r}, which is neither "
                "numeric nor categorical. The training pipeline encodes only those "
                "two, and will not guess a third."
            )
        kinds[name] = definition.kind
    return kinds


def resolve_target(name: ResearchTarget) -> TargetDefinition:
    """Return the definition for a target, or explain that it is unknown.

    Raised as a ``KeyError``-free explicit error so the service can turn it into
    a 422 naming the targets that do exist.
    """
    definition = TARGET_INDEX.get(name)
    if definition is None:
        raise LookupError(
            f"unknown research target {name!r}; available targets: "
            + ", ".join(target.name.value for target in TARGET_CATALOG)
        )
    return definition


@dataclass(frozen=True)
class TwinFeatureSource:
    """How one feature column is obtained from a digital twin state.

    Frozen and data-only, for the reason :class:`FeatureDefinition` is: an
    operational prediction reads the *current* twin rather than re-reading the
    records a training row was built from, so the mapping between a twin field and
    a column has to be inspectable without running the code that applies it. A
    reader who cannot see which twin field a column came from cannot check that
    the number on the screen describes the pan in front of them.
    """

    #: The column this fills.
    name: str
    #: The :class:`~app.models.digital_twin_state.DigitalTwinState` field read, for
    #: a direct read. ``None`` for a column computed from the twin.
    twin_field: str | None
    #: How the value is obtained: a straight read of ``twin_field``, or a
    #: deterministic computation from the twin's own quantities.
    method: str
    #: Why a column is in this table rather than derived on demand.
    justification: str


def _twin_read(name: str, twin_field: str, justification: str) -> TwinFeatureSource:
    return TwinFeatureSource(
        name=name, twin_field=twin_field, method="read", justification=justification
    )


def _twin_derived(name: str, method: str, justification: str) -> TwinFeatureSource:
    return TwinFeatureSource(
        name=name, twin_field=None, method=method, justification=justification
    )


#: Columns an operational prediction reads straight off the current twin.
#:
#: A twin is a joining of the newest pan observation, the newest relevant weather
#: record and the pan's static properties, so it carries the measured state and
#: the forecast in force. Each entry names the twin field it reads, which is the
#: field Module 05 publishes under that name.
#:
#: Only the *weather* ambient temperature and humidity appear here. The twin takes
#: ambient conditions from the weather record, so the observation's own ambient
#: reading is not on it, and those two columns are absent from an operational
#: prediction rather than filled with the weather reading that happens to be
#: nearby. They are different measurements and substituting one for the other
#: would change what a column means between training and serving.
TWIN_SOURCED_FEATURES: tuple[TwinFeatureSource, ...] = (
    _twin_read(
        "current_salinity_g_l",
        "estimated_salinity_g_l",
        "The twin's measured brine salinity, the same quantity the anchor "
        "observation supplied to a training row.",
    ),
    _twin_read(
        "water_depth_cm",
        "water_depth_cm",
        "The twin's measured brine depth.",
    ),
    _twin_read(
        "brine_temperature_c",
        "brine_temperature_c",
        "The twin's measured brine temperature, copied from the newest "
        "observation.",
    ),
    _twin_read(
        "weather_ambient_temperature_c",
        "ambient_temperature_c",
        "The twin's ambient temperature, which Module 05 takes from the weather "
        "record in force at the anchor.",
    ),
    _twin_read(
        "weather_humidity_percent",
        "humidity_percent",
        "The twin's relative humidity, from the same weather record.",
    ),
    _twin_read(
        "forecast_rainfall_mm",
        "forecast_rainfall_mm",
        "The forecast rainfall in force, never the rainfall that fell.",
    ),
    _twin_read(
        "forecast_rainfall_probability_percent",
        "rainfall_probability_percent",
        "Read from the same forecast record as the forecast depth.",
    ),
    _twin_read(
        "pan_area_m2",
        "pan_area_m2",
        "The pan's registered area, a static property the twin carries.",
    ),
)


#: Columns computed from the twin's own quantities at prediction time.
#:
#: Each is a deterministic function of values the twin already holds, so computing
#: it introduces no information the twin does not have. The calendar columns come
#: from the twin's ``as_of`` through the same helpers the builder used, and the
#: physics columns come from Module 06's mass balance run on the twin's brine
#: state and the forecast it carries, so an operational row and a training row
#: describe the same quantities computed the same way.
TWIN_DERIVED_FEATURES: tuple[TwinFeatureSource, ...] = (
    _twin_derived(
        "hour_of_day_utc",
        "calendar",
        "The hour of the twin's as_of, which is the same anchor instant a "
        "training row would have used.",
    ),
    _twin_derived(
        "day_of_year",
        "calendar",
        "The day of year of the twin's as_of.",
    ),
    _twin_derived(
        "season",
        "calendar",
        "The calendar band of the twin's as_of, via the builder's own season map.",
    ),
    _twin_derived(
        "physics_predicted_post_rain_salinity_g_l",
        "mass_balance",
        "Module 06's balance run on the twin's area, depth, salinity and the "
        "forecast in force. The four inputs are all twin fields.",
    ),
    _twin_derived(
        "physics_predicted_dilution_percent",
        "mass_balance",
        "The same balance's dilution percentage.",
    ),
    _twin_derived(
        "physics_prediction_rainfall_source",
        "mass_balance",
        "Always 'forecast' when the balance runs, because the twin's rainfall "
        "input is the forecast and never an observation.",
    ),
)

#: Every column an operational twin-sourced row can produce, by name.
TWIN_FEATURE_INDEX: dict[str, TwinFeatureSource] = {
    source.name: source
    for source in (*TWIN_SOURCED_FEATURES, *TWIN_DERIVED_FEATURES)
}


def _post_event_columns() -> frozenset[str]:
    """Column names whose value is a measurement taken *after* the anchor.

    Built from the two lists this module already documents rather than written out
    afresh, so it cannot drift from the builder's own accounting:

    * the outcome fields in :data:`EXCLUDED_FUTURE_FIELDS`, which the builder
      leaves out of every row for exactly this reason, and
    * the label column of every target that is *measured*. The derived
      operational-risk target is the exception: its label is computed from
      quantities known at the anchor, so ``label_operational_risk`` is not a
      post-event measurement and is deliberately not in the set.

    An outcome leaf whose name is also a catalogue feature is left out. The
    outcomes record carries a ``forecast_rainfall_mm`` of its own, meaning the
    forecast that was in force when the outcome was recorded rather than the one
    in force at the anchor; the catalogue feature of that name is the anchor's.
    A column name alone cannot tell the two apart, and the reading an
    operational row can actually carry is the feature's, so guessing the
    measurement reading here would refuse a model for the wrong reason.
    """
    outcomes = {
        field.split(".", 1)[1]
        for field, _ in EXCLUDED_FUTURE_FIELDS
        if field.startswith("actual_outcomes.") and field.split(".", 1)[1] not in FEATURE_INDEX
    }
    measured_labels = {
        target.label_column for target in TARGET_CATALOG if not target.requires_risk_definition
    }
    return frozenset(outcomes | measured_labels)


#: Names a model may be fitted *against* but may never declare as a feature.
#:
#: A research dataset legitimately contains these: they are its target, or the
#: outcomes it was built to explain. A prediction is made before any of them
#: exists, so a model that declares one as a feature is asking to be scored on
#: the answer. This set is what operational prediction checks its model's declared
#: features against, separately from the pipeline's broader exclusion list,
#: because the two refusals mean different things: a key column is excluded as an
#: identifier, one of these as knowledge of the future.
POST_EVENT_COLUMNS: frozenset[str] = _post_event_columns()


__all__ = [
    "ANCHOR_KEY",
    "BUILDER_SCHEMA_VERSION",
    "DEFAULT_LOOKBACK_HOURS",
    "DEFAULT_MAX_SOURCE_RECORDS",
    "EXCLUDED_FUTURE_FIELDS",
    "EXCLUDED_KEY_FIELDS",
    "FEATURE_CATALOG",
    "FEATURE_INDEX",
    "LABEL_COLUMNS",
    "MIN_HORIZON_HOURS",
    "MISSING_REASON_COLUMNS",
    "POST_EVENT_COLUMNS",
    "ROW_KEYS",
    "ROW_KEY_NAMES",
    "SEASON_BANDS",
    "TARGET_CATALOG",
    "TARGET_INDEX",
    "TWIN_DERIVED_FEATURES",
    "TWIN_FEATURE_INDEX",
    "TWIN_SOURCED_FEATURES",
    "FeatureDefinition",
    "RowKeyDefinition",
    "TargetDefinition",
    "TwinFeatureSource",
    "feature_names",
    "resolve_target",
    "season_for",
]
