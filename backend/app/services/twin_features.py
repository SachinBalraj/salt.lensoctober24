"""Assemble a model's feature row from the current digital twin state.

An operational prediction answers a question about a pan as it stands now, so its
inputs have to be the system's current belief about that pan. That belief is the
digital twin (Module 05): the newest pan observation, the newest relevant weather
record, and the pan's static properties, joined, with every gap reported.

This module turns a twin into exactly the feature columns a model declares, and
nothing else. Three rules, and they are the whole design.

**The twin is the only source.** Every value here is read off the twin or computed
from the twin's own quantities. Nothing is read from the request, and nothing is
re-read from the records a training row was built from. A prediction made from a
different assembly than the model was fitted on would be returned as a risk score
while describing something else.

**Exactly the declared columns.** The row handed to the estimator has the model's
``feature_names`` as its columns, in that order, and no others. A column the model
did not declare cannot be smuggled in, and a declared column cannot be quietly
omitted.

**A gap stays a gap.** A twin quantity nobody measured is ``None``, and the reason
the twin itself recorded is carried through. The model's fitted preprocessor
imputes it, which is the imputation the model was fitted with. Nothing here
invents a value, and no fallback quantity is read from somewhere else in the
system to fill a hole -- a column that means "rainfall over the last 24 hours" is
not made true by today's depth.

A column a twin cannot supply is therefore *absent*, not wrong. The row says which,
so the prediction that comes back can be read with its own weakness visible: a
number whose every input was imputed is not evidence about the pan, and
:meth:`TwinFeatureRow.is_fully_observed` is what lets a caller refuse it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.digital_twin_state import DigitalTwinState
from app.services.dataset_catalog import (
    FEATURE_INDEX,
    TWIN_DERIVED_FEATURES,
    TWIN_FEATURE_INDEX,
    TWIN_SOURCED_FEATURES,
    season_for,
)
from app.services.mass_balance import MassBalanceService, RainfallDilutionError

#: The three ways a column can be filled from a twin, and the two it cannot.
METHOD_READ = "read"
METHOD_CALENDAR = "calendar"
METHOD_MASS_BALANCE = "mass_balance"

#: Why a column a twin does not carry is absent, stated once and used everywhere.
#:
#: These columns are aggregates over a window of records -- recent rainfall, the
#: change since the previous observation -- or a second measurement of something
#: the twin already holds from a different source. A twin is a single instant, so
#: it has no window to aggregate and no previous row to difference against. The
#: reason is named rather than left blank because a blank reads as an oversight and
#: this is a property of what a twin is.
WINDOW_OR_HISTORY_REASON = (
    "This column aggregates records over a window, or differences against a "
    "previous observation. A digital twin is a single instant, so it holds no "
    "window to aggregate and no earlier row to compare with. The value is left "
    "absent and imputed from the training rows, not filled from another quantity."
)

OBSERVATION_ONLY_REASON = (
    "This column is the reading the pan observation recorded, and the twin takes "
    "this quantity from the weather record instead. The two are different "
    "measurements, so the weather reading is not substituted for it."
)

_MISSING_REASON_BY_METHOD = {
    METHOD_CALENDAR: "The twin state carries no as_of instant to read a calendar column from.",
    METHOD_MASS_BALANCE: (
        "The mass balance needs the pan's area, brine depth, brine salinity and a "
        "forecast rainfall depth, and the twin does not carry all four."
    ),
}


@dataclass(frozen=True)
class TwinFeatureRow:
    """One feature row built from one twin, and the account of how it was built.

    ``values`` is the row itself, keyed and ordered by the model's declared
    features. ``sources`` and ``absent`` are the audit trail beside it: where each
    value came from, and why each missing one is missing. Both are stored with the
    prediction, so a stored risk number can be checked against the twin state it
    was made from rather than taken on trust.
    """

    twin_id: str
    twin_as_of: Any
    values: dict[str, Any]
    sources: dict[str, str] = field(default_factory=dict)
    absent: dict[str, str] = field(default_factory=dict)
    used_physics: bool = False

    @property
    def observed_features(self) -> list[str]:
        """The declared columns that carry a real value from the twin."""
        return [name for name in self.values if self.values[name] is not None]

    @property
    def missing_features(self) -> list[str]:
        """The declared columns the twin could not supply."""
        return [name for name in self.values if self.values[name] is None]

    @property
    def is_fully_observed(self) -> bool:
        """Whether every declared column carries a value from the twin.

        False means the estimator will see imputed inputs for the missing columns.
        That is a legitimate prediction, and the row reports which columns were
        imputed, but a prediction in which *nothing* was observed says nothing
        about the pan and is refused by the caller rather than returned.
        """
        return not self.missing_features

    def snapshot(self) -> dict[str, Any]:
        """The stored form: the row, its provenance and its gaps.

        Keys sorted so the same twin and the same model produce the same bytes, and
        therefore the same snapshot id.
        """
        return {
            "twin_id": self.twin_id,
            "twin_as_of": self.twin_as_of.isoformat() if self.twin_as_of else None,
            "features": {name: self.values[name] for name in sorted(self.values)},
            "feature_sources": {name: self.sources[name] for name in sorted(self.sources)},
            "features_absent": {name: self.absent[name] for name in sorted(self.absent)},
            "used_physics_prediction": self.used_physics,
        }


class TwinFeatureAssembly:
    """Build feature rows from twin states."""

    def __init__(self, mass_balance: MassBalanceService | None = None) -> None:
        # Module 06's own service, so an operational row and a training row run the
        # same arithmetic. Substituted only in tests.
        self._mass_balance = mass_balance or MassBalanceService()

    def assemble(
        self, twin: DigitalTwinState, feature_names: list[str] | tuple[str, ...]
    ) -> TwinFeatureRow:
        """Build the row for one twin and one model's declared features.

        ``feature_names`` is used exactly as given: the returned ``values`` has
        those keys in that order. A name not in the feature catalogue is left
        absent rather than guessed at, so a model declaring a column this project
        cannot describe produces an imputed input and a reported gap, not a value
        pulled from an unrelated field.
        """
        values: dict[str, Any] = {}
        sources: dict[str, str] = {}
        absent: dict[str, str] = {}
        used_physics = False

        for name in feature_names:
            source = TWIN_FEATURE_INDEX.get(name)
            if source is None:
                absent[name] = self._uncatalogued_reason(name)
                values[name] = None
                continue

            if source.method == METHOD_READ:
                value, reason = self._read(twin, source)
                if value is None:
                    absent[name] = reason or self._twin_reason(twin, source.twin_field)
                else:
                    sources[name] = f"twin.{source.twin_field}"
            elif source.method == METHOD_CALENDAR:
                value = self._calendar(twin, name)
                sources[name] = "twin.as_of"
            else:
                computed, reason = self._physics(twin)
                value = None if computed is None else computed.get(name)
                if value is None:
                    absent[name] = reason or _MISSING_REASON_BY_METHOD[METHOD_MASS_BALANCE]
                else:
                    used_physics = True
                    sources[name] = "module_06.mass_balance"
            values[name] = value

        return TwinFeatureRow(
            twin_id=twin.twin_id,
            twin_as_of=twin.as_of,
            values=values,
            sources=sources,
            absent=absent,
            used_physics=used_physics,
        )

    # -- one column at a time ---------------------------------------------

    @staticmethod
    def _read(twin: DigitalTwinState, source: Any) -> tuple[Any, str | None]:
        """Read one twin field. ``None`` is a value-shaped absence, not an error."""
        assert source.twin_field is not None, "a read source must name its twin field"
        return getattr(twin, source.twin_field, None), None

    @staticmethod
    def _twin_reason(twin: DigitalTwinState, twin_field: str | None) -> str:
        """The twin's own reason for the gap, when it recorded one.

        Module 05 already explains each quantity it could not obtain, in a
        sentence an operator can act on. Reusing it means the prediction's gap is
        described in the same words as the twin's, rather than a second and
        possibly different explanation of the same absence.
        """
        if twin_field is not None:
            for entry in twin.missing_inputs:
                if entry.field == twin_field or entry.field.replace(
                    "estimated_salinity_g_l", "salinity_g_l"
                ) == twin_field:
                    return entry.reason
        definition = FEATURE_INDEX.get(twin_field or "")
        if definition is not None and definition.nullable_reason:
            return definition.nullable_reason
        return (
            f"The twin state for pan {twin.pan_id!r} records no value for "
            f"{twin_field!r}."
        )

    @staticmethod
    def _calendar(twin: DigitalTwinState, name: str) -> Any:
        """Calendar columns from the twin's ``as_of``.

        The same three helpers the builder uses for a training row's anchor, so a
        column means the same thing in both. A twin always has an ``as_of``: it is
        required to build one.
        """
        anchor = twin.as_of
        if name == "hour_of_day_utc":
            return float(anchor.hour)
        if name == "day_of_year":
            return float(anchor.timetuple().tm_yday)
        if name == "season":
            return season_for(anchor.month)
        raise LookupError(f"{name!r} is not a calendar column")

    def _physics(self, twin: DigitalTwinState) -> tuple[dict[str, Any] | None, str | None]:
        """Run Module 06's balance on the twin's own brine state and forecast.

        Returns the three physics columns together, because they are one
        computation: a run that produces a post-rain salinity produces the dilution
        percentage and the source label with it, and a run that cannot produce one
        cannot produce the others either. Returning them separately would let a row
        claim a dilution derived from a salinity that was never computed.

        The rainfall input is the twin's forecast, never observed rainfall, which
        is the choice that keeps the columns a feature rather than an answer.
        """
        area = twin.pan_area_m2
        depth = twin.water_depth_cm
        salinity = twin.estimated_salinity_g_l
        rainfall = twin.forecast_rainfall_mm
        if None in (area, depth, salinity, rainfall):
            return None, _MISSING_REASON_BY_METHOD[METHOD_MASS_BALANCE]
        if depth <= 0:
            return None, (
                "The twin records a brine depth of "
                f"{depth} cm, and there is no brine to dilute. The balance is not "
                "run on a pan with no water in it."
            )
        try:
            dilution = self._mass_balance.evaluate(
                area_m2=float(area),
                initial_depth_m=float(depth) / 100.0,
                initial_salinity_g_l=float(salinity),
                rainfall_mm=float(rainfall),
            )
        except RainfallDilutionError as exc:
            return None, f"The mass balance could not be evaluated: {exc}"
        return (
            {
                "physics_predicted_post_rain_salinity_g_l": dilution.post_rain_salinity_g_l,
                "physics_predicted_dilution_percent": dilution.dilution_percent,
                "physics_prediction_rainfall_source": "forecast",
            },
            None,
        )

    @staticmethod
    def _uncatalogued_reason(name: str) -> str:
        """Why a column the catalogue does not describe is left absent."""
        if name in ("recent_rainfall_mm", "recent_rainfall_record_count"):
            return WINDOW_OR_HISTORY_REASON
        if name in (
            "observation_ambient_temperature_c",
            "observation_humidity_percent",
            "salinity_change_g_l",
            "depth_change_cm",
            "hours_since_previous_observation",
        ):
            return OBSERVATION_ONLY_REASON
        return (
            f"{name!r} is not a column this project can build from a digital twin "
            "state, so no value is supplied for it. It is left absent and imputed "
            "from the training rows rather than filled from an unrelated field."
        )


def twin_sourced_columns() -> tuple[str, ...]:
    """Every column a twin-sourced row can produce, for a caller that wants to know."""
    return tuple(source.name for source in TWIN_SOURCED_FEATURES) + tuple(
        source.name for source in TWIN_DERIVED_FEATURES
    )


__all__ = [
    "METHOD_CALENDAR",
    "METHOD_MASS_BALANCE",
    "METHOD_READ",
    "OBSERVATION_ONLY_REASON",
    "WINDOW_OR_HISTORY_REASON",
    "TwinFeatureAssembly",
    "TwinFeatureRow",
    "twin_sourced_columns",
]
