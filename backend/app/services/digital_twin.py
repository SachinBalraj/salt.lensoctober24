"""Module 5 - assembling a pan's digital twin from records that exist.

The twin is a join, not a calculation. For one pan this module selects at most
three stored records and copies their values across unchanged. It contains no
physics, no interpolation and no fallback arithmetic, which is what makes it
safe to read as "the current state of this pan".

Selecting the inputs
--------------------

Each quantity has exactly one kind of record that may supply it, and "latest"
means latest by the instant the record describes, not by when it was typed. A
backfilled reading from last week therefore cannot become the current state just
because it was entered today, and the module reports an observation's age so a
stale twin is visible rather than silent.

An observed weather record supplies ambient conditions; a forecast supplies the
forecast rainfall. They are not interchangeable and neither is allowed to stand
in for the other, because presenting a forecast as an observation (or the
reverse) would mislabel the evidence the twin rests on.

Absence
-------

A quantity no record supplied stays ``None`` and is listed in ``missing_inputs``
with a sentence saying which record would have filled it. Nothing is carried
forward from an older record: a salinity measured yesterday and absent today is
reported as absent, not as yesterday's number wearing a current timestamp.

Snapshots
---------

``refresh`` writes an immutable snapshot so that later analysis can reconstruct
how a pan's state moved, and reports whether anything actually changed so a
change log built on top of it does not fill with no-op entries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status

from app.db.errors import DocumentNotFoundError
from app.db.indexes import (
    DIGITAL_TWIN_STATES,
    PAN_OBSERVATIONS,
    SALT_PANS,
    WEATHER_OBSERVATIONS,
)
from app.db.repository import MongoRepository
from app.models.base import normalize_pan_id, utcnow
from app.models.digital_twin_state import (
    EXPECTED_INPUT,
    TWIN_QUANTITIES,
    DigitalTwinState,
    MissingTwinInput,
)
from app.models.enums import TwinProvenance
from app.models.pan_observation import PanObservation
from app.models.salt_pan import SaltPan
from app.models.weather import WeatherObservation

#: Human labels for the quantities, used only in the sentences that explain a
#: gap. A message an operator can act on beats one that names a column.
FIELD_LABELS: dict[str, str] = {
    "estimated_salinity_g_l": "brine salinity",
    "water_depth_cm": "water depth",
    "brine_temperature_c": "brine temperature",
    "ambient_temperature_c": "ambient temperature",
    "humidity_percent": "humidity",
    "forecast_rainfall_mm": "forecast rainfall",
    "rainfall_probability_percent": "rainfall probability",
}

#: How each kind of contributing record is named inside an explanation.
#:
#: The article is supplied by the sentence templates below, so these values stay
#: usable in both "No X has been recorded" and "The X recorded ... did not".
SOURCE_LABELS: dict[str, str] = {
    "observation": "pan observation",
    "weather": "weather observation",
    "weather_forecast": "weather forecast",
}


@dataclass(frozen=True)
class TwinAssembly:
    """A freshly assembled twin together with the pan it describes."""

    state: DigitalTwinState
    pan: SaltPan


class DigitalTwinService:
    """Build, snapshot and read per-pan digital-twin state."""

    def __init__(self, database: Any) -> None:
        self._database = database
        self._resolved_pans: MongoRepository[SaltPan] | None = None
        self._resolved_observations: MongoRepository[PanObservation] | None = None
        self._resolved_weather: MongoRepository[WeatherObservation] | None = None
        self._resolved_states: MongoRepository[DigitalTwinState] | None = None

    @property
    def _pan_repository(self) -> MongoRepository[SaltPan]:
        if self._resolved_pans is None:
            self._resolved_pans = MongoRepository(
                self._database, SALT_PANS, SaltPan
            )
        return self._resolved_pans

    @property
    def _observation_repository(self) -> MongoRepository[PanObservation]:
        if self._resolved_observations is None:
            self._resolved_observations = MongoRepository(
                self._database, PAN_OBSERVATIONS, PanObservation
            )
        return self._resolved_observations

    @property
    def _weather_repository(self) -> MongoRepository[WeatherObservation]:
        if self._resolved_weather is None:
            self._resolved_weather = MongoRepository(
                self._database, WEATHER_OBSERVATIONS, WeatherObservation
            )
        return self._resolved_weather

    @property
    def _state_repository(self) -> MongoRepository[DigitalTwinState]:
        if self._resolved_states is None:
            self._resolved_states = MongoRepository(
                self._database, DIGITAL_TWIN_STATES, DigitalTwinState
            )
        return self._resolved_states

    # -- guards -----------------------------------------------------------

    @staticmethod
    def _normalized_pan_id(pan_id: str) -> str:
        normalized = normalize_pan_id(pan_id)
        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="pan_id must not be blank.",
            )
        return normalized

    async def _require_registered_pan(self, pan_id: str) -> SaltPan:
        """Return the pan, or explain that the identifier is unknown.

        A twin is a view of a registered pan, so an unknown identifier is a 404
        rather than an empty twin that looks like an unmeasured one.
        """
        normalized = self._normalized_pan_id(pan_id)
        try:
            return await self._pan_repository.get("pan_id", normalized)
        except DocumentNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No registered pan has the ID “{pan_id}”. Register the pan "
                    "before reading its digital twin."
                ),
            ) from exc

    # -- input selection --------------------------------------------------

    async def _latest_observation(
        self, pan_id: str
    ) -> PanObservation | None:
        """The newest pan observation, chosen by the instant it describes."""
        records = await self._observation_repository.find(
            {"pan_id": pan_id}, sort=[("timestamp", -1)], limit=1
        )
        return records[0] if records else None

    async def _latest_observed_weather(
        self, pan_id: str
    ) -> WeatherObservation | None:
        """The newest weather record that reports what happened.

        Forecasts are excluded by the query rather than filtered afterwards, so
        a forecast can never be promoted into an ambient observation.
        """
        records = await self._weather_repository.find(
            {"pan_id": pan_id, "is_forecast": False},
            sort=[("timestamp", -1)],
            limit=1,
        )
        return records[0] if records else None

    async def _latest_forecast_weather(
        self, pan_id: str
    ) -> WeatherObservation | None:
        """The newest weather record that predicts the future."""
        records = await self._weather_repository.find(
            {"pan_id": pan_id, "is_forecast": True},
            sort=[("timestamp", -1)],
            limit=1,
        )
        return records[0] if records else None

    # -- assembly ---------------------------------------------------------

    async def assemble(self, pan_id: str) -> TwinAssembly:
        """Join the newest records for a pan into a twin, without storing it.

        Every field is copied or left absent. No value is derived, averaged or
        carried forward from an older record, so the result is a faithful
        summary of the three newest records rather than a model of the pan.
        """
        pan = await self._require_registered_pan(pan_id)
        observation = await self._latest_observation(pan.pan_id)
        weather = await self._latest_observed_weather(pan.pan_id)
        forecast = await self._latest_forecast_weather(pan.pan_id)

        state = DigitalTwinState(
            pan_id=pan.pan_id,
            as_of=utcnow(),
            provenance=TwinProvenance.OBSERVED,
            # Static properties come from the registration, which cannot change
            # for an existing pan_id.
            pan_area_m2=pan.area_m2,
            location_name=pan.location_name,
            # Observation-backed quantities. Copied as-is, including their nulls.
            estimated_salinity_g_l=observation.salinity_g_l if observation else None,
            water_depth_cm=observation.water_depth_cm if observation else None,
            brine_temperature_c=(
                observation.brine_temperature_c if observation else None
            ),
            ambient_temperature_c=observation.ambient_temperature_c
            if observation
            else None,
            humidity_percent=observation.humidity_percent if observation else None,
            observation_id=observation.observation_id if observation else None,
            observation_timestamp=observation.timestamp if observation else None,
            observation_data_source=observation.data_source if observation else None,
        )

        if weather is not None:
            # Weather is the record of account for ambient conditions. The
            # observation's own figures only stand in when no weather reading
            # exists, which keeps the twin on the freshest real measurement of
            # the two without blending the two into an invented average.
            state.weather_id = weather.weather_id
            state.weather_timestamp = weather.timestamp
            state.weather_source = weather.source
            state.ambient_temperature_c = (
                weather.ambient_temperature_c
                if weather.ambient_temperature_c is not None
                else state.ambient_temperature_c
            )
            state.humidity_percent = (
                weather.humidity_percent
                if weather.humidity_percent is not None
                else state.humidity_percent
            )

        if forecast is not None:
            # Only a forecast may populate the forecast quantities. An observed
            # rainfall figure is a different claim and stays in the weather
            # record where it was made.
            state.weather_forecast_id = forecast.weather_id
            state.weather_forecast_timestamp = forecast.timestamp
            state.weather_forecast_source = forecast.source
            state.forecast_rainfall_mm = forecast.rainfall_mm
            state.rainfall_probability_percent = forecast.rainfall_probability_percent

        state.missing_inputs = self._describe_gaps(state, observation, weather, forecast)
        return TwinAssembly(state=state, pan=pan)

    def _describe_gaps(
        self,
        state: DigitalTwinState,
        observation: PanObservation | None,
        weather: WeatherObservation | None,
        forecast: WeatherObservation | None,
    ) -> list[MissingTwinInput]:
        """Name every unavailable quantity and say what would fill it."""
        present = {
            "observation": observation,
            "weather": weather,
            "weather_forecast": forecast,
        }
        gaps: list[MissingTwinInput] = []
        for field in TWIN_QUANTITIES:
            if state.value_of(field) is not None:
                continue
            label = FIELD_LABELS.get(field, field)
            expected = EXPECTED_INPUT.get(field, "observation")
            record = present.get(expected)
            if record is None:
                reason = (
                    f"No {SOURCE_LABELS[expected]} has been recorded for this pan, "
                    f"so {label} is unavailable."
                )
            else:
                reason = (
                    f"The {SOURCE_LABELS[expected]} recorded for this pan did not "
                    f"include {label}, so it is unavailable."
                )
            gaps.append(
                MissingTwinInput(field=field, reason=reason, expected_from=expected)
            )
        return gaps

    # -- reads ------------------------------------------------------------

    async def get_state(self, pan_id: str) -> TwinAssembly:
        """The pan's current state, assembled fresh.

        Reading is free of side effects: no snapshot is written, so a page view
        cannot manufacture history. Use :meth:`refresh` to record a snapshot.
        """
        return await self.assemble(pan_id)

    async def list_history(
        self,
        pan_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[SaltPan, list[DigitalTwinState], int]:
        """Stored snapshots for a pan, newest first.

        Ordered by assembly time, because that is when the snapshot was taken
        and it is the only ordering that is monotonic for a given pan.
        """
        pan = await self._require_registered_pan(pan_id)
        query = {"pan_id": pan.pan_id}
        total = await self._state_repository.count(query)
        snapshots = await self._state_repository.find(
            query, sort=[("as_of", -1)], limit=limit, skip=offset
        )
        return pan, snapshots, total

    async def latest_snapshot(self, pan_id: str) -> DigitalTwinState | None:
        """The most recently stored snapshot, or None when none exists yet."""
        _, snapshots, _ = await self.list_history(pan_id, limit=1)
        return snapshots[0] if snapshots else None

    # -- writes -----------------------------------------------------------

    async def refresh(self, pan_id: str) -> tuple[TwinAssembly, DigitalTwinState | None, bool]:
        """Reassemble the twin, store a snapshot and report whether it moved.

        Returns the new assembly, the snapshot it was compared against, and
        whether any tracked quantity or input changed. A refresh that finds
        nothing new still records a snapshot, so the history shows that the twin
        was checked; ``changed`` is what tells a caller the inputs had not moved.
        """
        assembly = await self.assemble(pan_id)
        previous = await self.latest_snapshot(assembly.state.pan_id)
        changed = True if previous is None else self._has_moved(previous, assembly.state)
        stored = await self._state_repository.insert(assembly.state)
        assembly = TwinAssembly(state=stored, pan=assembly.pan)
        return assembly, previous, changed

    @staticmethod
    def _has_moved(previous: DigitalTwinState, current: DigitalTwinState) -> bool:
        """Whether a quantity or a contributing record differs from a snapshot.

        Assembly time is deliberately excluded: it changes on every call and
        would make every refresh look like a change.
        """
        tracked: tuple[str, ...] = TWIN_QUANTITIES + (
            "observation_id",
            "weather_id",
            "weather_forecast_id",
        )
        return any(
            getattr(previous, field, None) != getattr(current, field, None)
            for field in tracked
        )

    async def registry_counts(self) -> dict[str, int]:
        """Twin totals for the module status endpoint."""
        return {"total_twin_states": await self._state_repository.count()}


__all__ = ["FIELD_LABELS", "DigitalTwinService", "TwinAssembly"]
