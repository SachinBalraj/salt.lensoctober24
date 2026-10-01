"""Module 3 - recording and reading pan observations.

Two invariants shape this module.

First, an observation is evidence. A field the operator did not measure is
stored as ``None`` and stays ``None`` all the way to the chart, so a gap in the
record reads as a gap rather than as an invented number.

Second, the pan's "current condition" is a denormalised copy of its newest
observation. It is written in the same operation that stores the reading, and
it only ever moves forward: a backdated entry does not overwrite a newer one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException, status

from app.config import settings
from app.db.errors import DocumentNotFoundError
from app.db.indexes import PAN_OBSERVATIONS, SALT_PANS
from app.db.repository import MongoRepository
from app.models.base import normalize_pan_id
from app.models.enums import ResearchDataSource
from app.models.pan_observation import PanObservation
from app.models.salt_pan import LatestObservationState, SaltPan
from app.schemas.observation import (
    SENSOR_UNAVAILABLE_MESSAGE,
    PanObservationCreate,
    PanObservationListResponse,
    PanObservationRead,
)


class ObservationService:
    """Manual observation entry, history retrieval and latest-state upkeep."""

    def __init__(self, database: Any) -> None:
        self._database = database
        self._resolved_pans: MongoRepository[SaltPan] | None = None
        self._resolved_observations: MongoRepository[PanObservation] | None = None

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

        Observations reference a registered pan, so an unknown identifier is a
        404 rather than a silently orphaned reading.
        """
        normalized = self._normalized_pan_id(pan_id)
        try:
            return await self._pan_repository.get("pan_id", normalized)
        except DocumentNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No registered pan has the ID “{pan_id}”. Register the pan "
                    "before recording observations for it."
                ),
            ) from exc

    def _reject_sensor_source(self, data_source: ResearchDataSource) -> None:
        """Refuse a sensor reading while no gateway is connected.

        Accepting it would put an unverifiable provenance claim into the
        research record, which is exactly what this project must not do.
        """
        if data_source is not ResearchDataSource.SENSOR:
            return
        if not settings.sensor_ingest_enabled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=SENSOR_UNAVAILABLE_MESSAGE,
            )

    # -- writes -----------------------------------------------------------

    async def create(self, payload: PanObservationCreate) -> PanObservation:
        """Store one reading and advance the pan's latest observed state."""
        self._reject_sensor_source(payload.data_source)
        pan = await self._require_registered_pan(payload.pan_id)

        observation = PanObservation(
            pan_id=pan.pan_id,
            timestamp=payload.resolved_timestamp(),
            salinity_g_l=payload.salinity_g_l,
            water_depth_cm=payload.water_depth_cm,
            brine_temperature_c=payload.brine_temperature_c,
            humidity_percent=payload.humidity_percent,
            ambient_temperature_c=payload.ambient_temperature_c,
            data_source=payload.data_source,
            observer=payload.observer,
            evidence_source=payload.evidence_source,
            note=payload.note,
        )
        stored = await self._observation_repository.insert(observation)
        await self._advance_latest_state(pan, stored)
        return stored

    async def _advance_latest_state(
        self, pan: SaltPan, observation: PanObservation
    ) -> None:
        """Copy the reading onto the pan, but only if it is the newest.

        Backfilling last week's readings is normal practice; it must not make
        the registry display a week-old condition as if it were current.
        """
        current = pan.latest_observation
        if current is not None and current.timestamp > observation.timestamp:
            return
        latest = LatestObservationState.from_observation(observation)
        payload = latest.model_dump(mode="python")
        payload["data_source"] = latest.data_source.value
        await self._pan_repository.update_one(
            "pan_id", pan.pan_id, {"latest_observation": payload}
        )

    # -- reads ------------------------------------------------------------

    async def get(self, observation_id: str) -> PanObservation:
        try:
            return await self._observation_repository.get(
                "observation_id", observation_id
            )
        except DocumentNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No observation has the ID “{observation_id}”.",
            ) from exc

    async def list_observations(
        self,
        *,
        pan_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> PanObservationListResponse:
        """Return readings newest first, optionally narrowed to a pan and window.

        ``limit`` defaults high because a trend chart needs the series rather
        than a first page. The response still reports the true ``total`` so a
        caller can tell that its window was truncated instead of assuming the
        chart is the whole history.
        """
        if start is not None and end is not None and start > end:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="start must not be later than end.",
            )

        query: dict[str, Any] = {}
        if pan_id:
            await self._require_registered_pan(pan_id)
            query["pan_id"] = self._normalized_pan_id(pan_id)

        window: dict[str, Any] = {}
        if start is not None:
            window["$gte"] = start
        if end is not None:
            window["$lte"] = end
        if window:
            query["timestamp"] = window

        total = await self._observation_repository.count(query)
        observations = await self._observation_repository.find(
            query, sort=[("timestamp", -1)], limit=limit, skip=offset
        )
        timestamps = [item.timestamp for item in observations]
        return PanObservationListResponse(
            items=[PanObservationRead.from_observation(item) for item in observations],
            total=total,
            limit=limit,
            offset=offset,
            earliest_timestamp=min(timestamps) if timestamps else None,
            latest_timestamp=max(timestamps) if timestamps else None,
        )

    async def registry_counts(self) -> dict[str, int]:
        """Observation totals for the module status endpoint."""
        return {
            "total_observations": await self._observation_repository.count(),
        }
