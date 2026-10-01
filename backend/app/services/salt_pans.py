"""Salt-pan registry operations (Module 2).

Every method here operates on operator-entered registration data only. No
value is inferred, defaulted or synthesised: if the operator did not supply a
coordinate, the pan is stored without one rather than being geolocated to a
placeholder position.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException, status

from app.db.indexes import SALT_PANS
from app.db.repository import MongoRepository
from app.models import SaltPan
from app.models.base import normalize_pan_id, utcnow
from app.schemas.salt_pan import (
    PanStatus,
    SaltPanListResponse,
    SaltPanRead,
    SaltPanRegistration,
    SaltPanUpdate,
)

#: Scalar fields an operator may change after registration.
EDITABLE_FIELDS: tuple[str, ...] = (
    "pan_name",
    "location",
    "latitude",
    "longitude",
    "area_m2",
    "normal_depth_cm",
    "target_harvest_salinity_g_l",
    "risk_thresholds",
)


def _rebuild(existing: SaltPan, **overrides: Any) -> SaltPan:
    """Re-validate a stored pan with operator-supplied changes applied.

    Constructing a whole ``SaltPan`` (rather than issuing a partial ``$set``)
    means a partial coordinate edit cannot leave a pan holding only one of
    latitude/longitude, and ``updated_at`` is always re-checked against
    ``created_at``.
    """
    values: dict[str, Any] = {
        "pan_id": existing.pan_id,
        "pan_name": existing.pan_name,
        "location_name": existing.location_name,
        "latitude": existing.latitude,
        "longitude": existing.longitude,
        "area_m2": existing.area_m2,
        "normal_depth_cm": existing.normal_depth_cm,
        "target_harvest_salinity_g_l": existing.target_harvest_salinity_g_l,
        "risk_thresholds": existing.risk_thresholds,
        "latest_observation": existing.latest_observation,
        "is_active": existing.is_active,
        "created_at": existing.created_at,
        "deactivated_at": existing.deactivated_at,
    }
    for field, value in overrides.items():
        if field == "location":
            values["location_name"] = value
        elif field == "risk_thresholds":
            # An update replaces the block whole rather than merging into it.
            # Merging would make "I removed the flood depth" inexpressible: the
            # only way to unset a limit has to be to send it as null, and that
            # would otherwise be indistinguishable from leaving it alone.
            values["risk_thresholds"] = value.to_model()
        else:
            values[field] = value
    values["updated_at"] = utcnow()
    return SaltPan(**values)


class SaltPanService:
    """Registration, lookup, editing and deactivation of salt pans."""

    def __init__(self, database: Any) -> None:
        """Store the database handle, or a zero-argument callable that returns it.

        A callable is resolved on first use rather than at construction so
        that request-body validation runs before an unavailable database is
        reported. That keeps a malformed payload a 422 instead of masking it
        with a 503 from the persistence layer.
        """
        self._database = database
        self._resolved_repository: MongoRepository[SaltPan] | None = None

    @property
    def _repository(self) -> MongoRepository[SaltPan]:
        if self._resolved_repository is None:
            self._resolved_repository = MongoRepository(
                self._database, SALT_PANS, SaltPan
            )
        return self._resolved_repository

    async def registry_counts(self) -> dict[str, int]:
        """Return the active and deactivated pan counts for status reporting."""
        return {
            "total_active": await self._repository.count({"is_active": {"$ne": False}}),
            "total_inactive": await self._repository.count({"is_active": False}),
        }

    def _normalize(self, pan_id: str) -> str:
        normalized = normalize_pan_id(pan_id)
        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="pan_id must not be blank.",
            )
        return normalized

    async def register(self, payload: SaltPanRegistration) -> SaltPan:
        pan = SaltPan(
            pan_id=payload.pan_id,
            pan_name=payload.pan_name,
            location_name=payload.location,
            latitude=payload.latitude,
            longitude=payload.longitude,
            area_m2=payload.area_m2,
            normal_depth_cm=payload.normal_depth_cm,
            target_harvest_salinity_g_l=payload.target_harvest_salinity_g_l,
            # Carried through from the request rather than constructed empty, so a
            # pan can be registered with the operator's own limits already on it.
            # A registration that omits them stores a block of nulls, which reads
            # as "not configured" everywhere downstream -- the same state as a pan
            # registered before this was settable, so nothing has to distinguish
            # the two cases.
            risk_thresholds=payload.risk_thresholds.to_model(),
        )
        return await self._repository.insert(pan)

    async def list_pans(
        self,
        *,
        pan_status: PanStatus = PanStatus.ACTIVE,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> SaltPanListResponse:
        """Return a page of pans plus registry-wide lifecycle counts.

        ``total_active``/``total_inactive`` describe the whole registry and are
        deliberately not narrowed by ``search`` so the dashboard tiles stay
        stable while the operator types.
        """
        query: dict[str, Any] = {}
        if pan_status is PanStatus.ACTIVE:
            query["is_active"] = True
        elif pan_status is PanStatus.INACTIVE:
            query["is_active"] = False
        if search:
            pattern = re.escape(search)
            query["$or"] = [
                {"pan_id": {"$regex": pattern, "$options": "i"}},
                {"pan_name": {"$regex": pattern, "$options": "i"}},
                {"location_name": {"$regex": pattern, "$options": "i"}},
            ]

        total = await self._repository.count(query)
        # Pans registered before the lifecycle flag existed count as active.
        total_active = await self._repository.count({"is_active": {"$ne": False}})
        total_inactive = await self._repository.count({"is_active": False})
        pans = await self._repository.find(
            query,
            sort=[("pan_id", 1)],
            skip=offset,
            limit=limit,
        )
        return SaltPanListResponse(
            items=[SaltPanRead.from_pan(pan) for pan in pans],
            total=total,
            limit=limit,
            offset=offset,
            total_active=total_active,
            total_inactive=total_inactive,
        )

    async def get_pan(self, pan_id: str) -> SaltPan:
        return await self._repository.get("pan_id", self._normalize(pan_id))

    async def update_pan(self, pan_id: str, payload: SaltPanUpdate) -> SaltPan:
        changes = payload.editable_fields()
        if not changes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "No editable fields were supplied. Provide at least one of: "
                    f"{', '.join(EDITABLE_FIELDS)}."
                ),
            )
        existing = await self.get_pan(pan_id)
        updated = _rebuild(existing, **changes)
        return await self._repository.replace("pan_id", existing.pan_id, updated)

    async def deactivate_pan(self, pan_id: str) -> SaltPan:
        existing = await self.get_pan(pan_id)
        if not existing.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Salt pan '{existing.pan_id}' is already deactivated. "
                    "Deactivation is a one-way transition in Module 2."
                ),
            )
        now = utcnow()
        deactivated = _rebuild(
            existing,
            is_active=False,
            deactivated_at=now,
        )
        return await self._repository.replace("pan_id", existing.pan_id, deactivated)
