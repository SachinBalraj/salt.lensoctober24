"""Phase 7 - seed the registry from the live March event, and write VERSION.json.

Reads the one real rainfall event out of MongoDB, replays it through the actual
:class:`~app.services.mass_balance.MassBalanceService`, and files it in the
experiment registry as what it is: a single operator-reported event with a
brine transfer of unrecorded volume.

Two things this script will not do, and they are the reason it is a script rather
than a set of hand-written files.

**It will not verify anything.** Every ``*_verified`` field it writes is derived
from what the source record already claims. The source says ``verification_state:
reported``, so the registry says the same. There is no path here that upgrades an
event to independently verified, because no reading in this project has been
verified by anyone.

**It will not let the replay be taken for a prediction made before the fact.**
The event's prediction was computed by the existing Module 20 replay against a
rainfall total that was reported afterwards, and the registry records that
plainly: ``prediction_made_at`` is stamped at the event instant and the manifest
records that the run happened later. The event still classifies as
``INCOMPLETE_EVIDENCE_EVENT``, which is the correct answer and is not a bug in the
seeding.

Run from the backend directory so ``backend/.env`` is picked up:

    backend/.venv/bin/python ../scripts/seed_experiment_registry.py

``--offline`` builds the manifest without a database connection, which is what CI
does, and is why the manifest's ``unavailable`` block is a first-class field rather
than an oversight.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.experiments.classifier import classify_event
from app.experiments.freeze import freeze_manifest
from app.experiments.metrics import validate_events
from app.experiments.models import (
    InterventionRecord,
    MeasurementSource,
    VerificationState,
)
from app.experiments.paths import ExperimentPaths
from app.experiments.store import ExperimentStore, write_events_csv

#: The event this project actually has. Named explicitly rather than taken as
#: "the first event found", so re-running against a database that has since gained
#: events does not quietly change which record the registry claims to describe.
MARCH_EVENT_ID = "ev-249b575de8234ff09b341e525ef5d4a1"

#: Maps the field-validation service's vocabulary onto the registry's. Kept
#: explicit because the two use different words for the same idea and a silent
#: coercion would let a new value slip through untranslated.
_VERIFICATION_TO_REGISTRY = {
    "reported": VerificationState.OPERATOR_REPORTED,
    "operator_reported": VerificationState.OPERATOR_REPORTED,
    "unverified": VerificationState.UNVERIFIED,
    "independently_verified": VerificationState.INDEPENDENTLY_VERIFIED,
}

_SOURCE_TO_REGISTRY = {
    "manual": MeasurementSource.MANUAL_FIELD,
    "manual_field": MeasurementSource.MANUAL_FIELD,
    "instrument": MeasurementSource.INSTRUMENT,
    "operator_reported": MeasurementSource.OPERATOR_REPORTED,
    "demonstration": MeasurementSource.DEMONSTRATION,
    "forecast": MeasurementSource.DEMONSTRATION,
}


def _map_verification(value: Any) -> VerificationState:
    """Translate, or refuse. An unmapped value becomes ``UNVERIFIED``.

    Defaulting to the strictest state rather than a permissive one matters: a new
    vocabulary value that lands here must not be guessed into a category that would
    make an event count as evidence.
    """
    if not isinstance(value, str):
        return VerificationState.UNVERIFIED
    return _VERIFICATION_TO_REGISTRY.get(value.strip().lower(), VerificationState.UNVERIFIED)


def _map_source(value: Any) -> MeasurementSource:
    """Translate, defaulting to the least trustworthy source.

    ``DEMONSTRATION`` rather than ``MANUAL_FIELD`` for the same reason: an
    unrecognised provenance must never read as a field measurement.
    """
    if not isinstance(value, str):
        return MeasurementSource.DEMONSTRATION
    return _SOURCE_TO_REGISTRY.get(value.strip().lower(), MeasurementSource.DEMONSTRATION)


async def _load_source_event(database: Any) -> dict[str, Any]:
    from app.db.collections import RAINFALL_EVENTS

    document = await database[RAINFALL_EVENTS].find_one({"event_id": MARCH_EVENT_ID})
    if document is None:
        raise SystemExit(
            f"No rainfall event {MARCH_EVENT_ID!r} in the database. This script "
            "seeds the registry from a real recorded event and will not invent "
            "one; run scripts/seed_field_validation.py first if the event is "
            "genuinely missing, and note that a seeded event is still a reported "
            "reference, not a verified measurement."
        )
    return document


async def _replay(document: dict[str, Any], *, area_m2: float) -> tuple[float, float]:
    """Run the real mass-balance service over the event's own figures.

    Uses :meth:`MassBalanceService.evaluate` rather than the formula, so the
    registry's predicted column is what the system would actually have produced
    for these inputs and nothing has to be kept in step by hand. Returns
    ``(salinity_g_l, depth_cm)``.

    ``area_m2`` comes from the pan record rather than being assumed, because area
    cancels out of the salinity ratio but not out of anything else the balance
    reports, and a hardcoded area would be a second source of truth.
    """
    from app.services.mass_balance import MassBalanceService

    result = MassBalanceService().evaluate(
        area_m2=area_m2,
        initial_depth_m=_require(document, "pre_event")["water_depth_cm"] / 100.0,
        initial_salinity_g_l=_require(document, "pre_event")["salinity_g_l"],
        rainfall_mm=document["actual_rainfall_mm"],
    )
    return float(result.post_rain_salinity_g_l), float(result.post_rain_depth_m * 100.0)


def _require(document: dict[str, Any], key: str) -> dict[str, Any]:
    """A nested block that must be present and populated.

    Raises rather than defaulting. ``pre_event`` carries the salinity and depth the
    replay is computed from, and a replay that ran on a substituted value would be
    a prediction of something other than what was recorded.
    """
    block = document.get(key)
    if not isinstance(block, dict):
        raise SystemExit(
            f"The source event has no {key!r} block, so the figures it should "
            "carry are unknown. Nothing is substituted for them."
        )
    for field in ("salinity_g_l", "water_depth_cm", "timestamp"):
        if block.get(field) is None:
            raise SystemExit(
                f"The source event's {key!r} block has no {field!r}. The replay "
                "needs it, and a default here would be a figure nobody measured."
            )
    return block


async def _pan_area(database: Any, pan_id: str) -> float:
    """The pan's own area, read from its record."""
    from app.db.collections import SALT_PANS

    pan = await database[SALT_PANS].find_one({"pan_id": pan_id})
    area = (pan or {}).get("area_m2")
    if area is None:
        raise SystemExit(
            f"Pan {pan_id!r} has no recorded area, and the balance cannot run "
            "without one. The area is not defaulted: a guessed area would change "
            "every volume the replay reports."
        )
    return float(area)


async def seed(*, offline: bool = False, root: Path | None = None) -> dict[str, Any]:
    """Build the registry and the frozen manifest. Returns a summary."""
    paths = ExperimentPaths(root=root) if root else ExperimentPaths()
    store = ExperimentStore(paths=paths)

    if offline:
        manifest = await freeze_manifest()
    else:
        manifest = await freeze_manifest(
            database=await _connected_database(), pan_ids=["pan_orginal"]
        )

    if offline:
        summary = {
            "seeded": False,
            "reason": (
                "Run offline, so no event was read and no event was written. The "
                "manifest records the database-derived fields as unavailable, "
                "which is the honest offline answer."
            ),
            "registry_root": str(paths.root),
            "manifest_hash": manifest.content_hash(),
        }
        _write_manifest(paths, manifest)
        return summary

    database = await _connected_database()
    document = await _load_source_event(database)
    area_m2 = await _pan_area(database, document["pan_id"])
    predicted_salinity, predicted_depth = await _replay(document, area_m2=area_m2)

    # Idempotent: an event already in the registry is left exactly as it is. The
    # seal is not re-writable, so re-running must not attempt it.
    pre_event = _require(document, "pre_event")
    post_event = document.get("post_event") or {}

    if store.exists(MARCH_EVENT_ID):
        event = store.get(MARCH_EVENT_ID)
        seeded = False
    else:
        store.register_event(
            event_id=MARCH_EVENT_ID,
            pan_id=document["pan_id"],
            date=document["event_timestamp"].date(),
            initial_salinity_g_l=pre_event["salinity_g_l"],
            initial_depth_cm=pre_event["water_depth_cm"],
            pre_event_timestamp=_aware(pre_event.get("timestamp")),
            pre_event_timestamp_verified=pre_event.get("timestamp_verified"),
            observation_source_id=pre_event.get("observation_id"),
            area_m2=area_m2,
            rainfall_mm=document["actual_rainfall_mm"],
            rainfall_is_forecast=False,
            rainfall_source=document.get("rainfall_source") or "operator reported",
            rainfall_observed=(
                document.get("rainfall_timestamp_verification") == "observed"
            ),
            notes=(
                "User-reported reference event for the field-validation dashboard. "
                f"Rainfall evidence: {document.get('rainfall_timestamp_evidence')}. "
                "The rainfall was reported by the same operator who filed the "
                "post-event reading, so it is not an independent input."
            ),
        )
        store.lock_prediction(
            MARCH_EVENT_ID,
            predicted_salinity_g_l=predicted_salinity,
            predicted_depth_cm=predicted_depth,
            rainfall_mm=document["actual_rainfall_mm"],
            # Stamped at the event instant. The replay was computed afterwards
            # from an after-the-fact rainfall total, which is why the event is
            # incomplete; dating the prediction earlier would misrepresent that.
            prediction_made_at=_aware(document["event_timestamp"]),
            model_name=manifest.simulation_model_name or "mass-balance",
            app_version=manifest.app_version or "unknown",
            git_commit=manifest.git_commit,
            manifest_hash=manifest.content_hash(),
        )
        for intervention in document.get("interventions") or []:
            store.record_intervention(
                MARCH_EVENT_ID, _to_intervention_record(intervention)
            )
        store.record_observation(
            MARCH_EVENT_ID,
            observed_salinity_g_l=post_event.get("salinity_g_l"),
            observed_depth_cm=post_event.get("water_depth_cm"),
            post_event_timestamp=_aware(post_event.get("timestamp")),
            post_event_timestamp_verified=post_event.get("timestamp_verified"),
            measurement_source=_map_source(post_event.get("source")),
            verification_state=_map_verification(document.get("verification_state")),
            independent_verification=False,
            verified_by=document.get("reported_by"),
        )
        event = store.get(MARCH_EVENT_ID)
        seeded = True

    csv_path = write_events_csv(store)
    report = validate_events(store.list_events())
    results_path = _write_results(paths, report, manifest)
    _write_manifest(paths, manifest)

    return {
        "seeded": seeded,
        "event_id": event.event_id,
        "category": classify_event(event).category.value,
        "reasons": list(classify_event(event).reasons),
        "validation_sufficient": report.sufficient,
        "validation_message": report.message,
        "csv": str(csv_path),
        "results": str(results_path),
        "manifest_hash": manifest.content_hash(),
        "registry_root": str(paths.root),
    }


def _to_intervention_record(document: dict[str, Any]) -> InterventionRecord:
    """Build the intervention, keeping an unrecorded volume unrecorded.

    The source stores ``volume_m3: None`` for this transfer. Writing ``0`` would
    assert nothing was moved, and the operator's note says the opposite: something
    was moved and its size was not written down. Those are different facts and only
    one of them is disqualifying.
    """
    volume = document.get("volume_m3")
    return InterventionRecord(
        kind=document.get("kind") or "other",
        direction=document.get("direction"),
        performed_at=_aware(document.get("performed_at")),
        volume_m3=volume,
        volume_recorded=volume is not None,
        described_by="field-operator",
        notes=document.get("description"),
    )


async def _connected_database() -> Any:
    from app.db.mongo import db

    await db.connect()
    if not db.connected:
        raise SystemExit(
            f"The database is not reachable ({db.unavailable_reason()}). Pass "
            "--offline to write the manifest alone; do not point this at a "
            "different database to work around it, because the event it finds "
            "would be a different event."
        )
    return db.get_db()


def _aware(value: Any) -> datetime | None:
    """Coerce to an aware UTC datetime, or ``None``.

    MongoDB returns naive datetimes; the store requires aware ones so that the
    prediction-before-observation comparison is meaningful. A value that cannot be
    read becomes ``None`` rather than today's date.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _write_manifest(paths: ExperimentPaths, manifest: Any) -> Path:
    paths.ensure()
    path = paths.manifest_file
    path.write_text(
        json.dumps(manifest.as_dict(), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return path


def _write_results(paths: ExperimentPaths, report: Any, manifest: Any) -> Path:
    paths.ensure()
    path = paths.results_dir / "validation.json"
    path.write_text(
        json.dumps(
            {
                "manifest_hash": manifest.content_hash(),
                "report": report.as_dict(),
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Write the manifest without a database, seeding no event.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Write to this registry root instead of the default.",
    )
    arguments = parser.parse_args()

    summary = await seed(offline=arguments.offline, root=arguments.root)
    print(json.dumps(summary, indent=2, default=str))

    if not summary.get("seeded") and not arguments.offline:
        print(
            "\nThe event was already registered and was left untouched. A sealed "
            "prediction cannot be rewritten, so re-running never changes it.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    asyncio.run(_main())
