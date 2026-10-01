"""Seed the North Pan field-validation reference event (Module 20).

Files one reported rainfall event for a registered pan: initial salinity
250 g/L, initial depth 20 cm, actual rainfall 90 mm, post-event report
20 g/L at 50 cm, and a brine-transfer-out intervention whose volume was not
recorded. Idempotent: a pan that already has an event with this name is left
untouched, so re-running the script never duplicates a historical record.

The instants this script writes are the ones the *report* claims, not records of
a collection: nothing here observes anything. That is why the event is filed with
``event_milestone`` left at ``unknown`` and with both snapshots carrying no
observation link and no observer, and why the notes say plainly that this is a
reference report. An event seeded this way can never read as a verified
measurement; the reconciliation routes are how a real source record is attached
to it afterwards.

Run from the backend directory so the application settings (MONGODB_URI etc.)
are picked up from ``backend/.env``:

    backend/.venv/bin/python ../scripts/seed_field_validation.py

The pan named by ``--pan-id`` (default ``pan_orginal``) must already be
registered, because the replay reads the pan's area and because a field report
must never invent a pan of its own.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

# The backend package lives one directory up from this script; put it on the
# import path so the seed can be run from anywhere, not only from inside
# backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.mongo import db
from app.models.field_validation import RainfallEvent
from app.schemas.field_validation import (
    RainfallEventCreate,
    RainfallTimestampResolutionCreate,
)
from app.services.field_validation import FieldValidationService

EVENT_NAME = "North Pan 14 Mar 2026 rainfall event"
EVENT_AT = datetime(2026, 3, 14, 18, 0, tzinfo=timezone.utc)

#: When the rain fell, per the North Pan field notebook. 23:30 IST.
RAIN_AT = datetime(2026, 3, 14, 18, 0, tzinfo=timezone.utc)
RAIN_EVIDENCE = "North Pan field notebook (manual field measurement)"

#: The value this seed used to hardcode, and the reason it is wrong. Kept here as
#: a named constant rather than deleted, because a reader who meets the old number
#: in a database or an old export needs to find where it came from and why it was
#: set aside. 19:30 IST, entered as a literal in an earlier version of this file
#: and labelled "manual rain gauge" with no gauge record ever produced to support
#: it. What the time was meant to mark is not established anywhere, so it is not
#: replayed and not shown as a measurement; it is preserved as superseded history.
#: Nothing in this script may write it back to an event.
SUPERSEDED_RAIN_AT = datetime(2026, 3, 14, 14, 0, tzinfo=timezone.utc)
SUPERSEDED_RAIN_SOURCE_CLAIM = "manual rain gauge"

#: Where the superseded instant came from, as an auditor would be told.
SUPERSEDED_RAIN_ORIGIN = (
    "scripts/seed_field_validation.py RAIN_AT literal (earlier version)"
)
SUPERSEDED_RAIN_REASON = (
    "No rain-gauge record supports 19:30 IST and no source establishes what the "
    "time was meant to mark. The North Pan field notebook records the rain at "
    "23:30 IST, which is the instant now in force. The earlier value is kept as "
    "audit history only and is not a measurement."
)


async def _retire_superseded_rain_at(
    service: FieldValidationService, event: RainfallEvent
) -> None:
    """Retire the old literal on an event that still carries it.

    Deliberately narrow. The only instant this script will touch is the one it
    itself planted, so a database whose instant was later set by an auditor is
    left alone -- otherwise a routine ``make seed`` would quietly undo a human
    decision. In particular the presence of a superseded 19:30 entry is *not*
    treated as licence to act again: it means the correction already happened.

    Running this repeatedly adds no second correction and no second history
    entry, and the old value is only ever retired here, never reinstated.
    """
    if event.rainfall_timestamp == SUPERSEDED_RAIN_AT:
        await service.resolve_rainfall_timestamp(
            event.event_id,
            RainfallTimestampResolutionCreate(
                timestamp=RAIN_AT,
                evidence_source=RAIN_EVIDENCE,
                superseded_origin=SUPERSEDED_RAIN_ORIGIN,
                superseded_source_claim=SUPERSEDED_RAIN_SOURCE_CLAIM,
                superseded_supporting_record=None,
                reason=SUPERSEDED_RAIN_REASON,
                corrected_by="seed_field_validation",
            ),
        )
        print(
            f"  retired the superseded 19:30 IST literal on {event.event_id}; the "
            f"notebook instant {RAIN_AT.isoformat()} is now in force."
        )
    elif event.rainfall_timestamp != RAIN_AT:
        print(
            f"  left untouched: {event.event_id} carries "
            f"{event.rainfall_timestamp.isoformat()}, which this script did not "
            "seed. Move it through the resolution endpoint with its own evidence."
        )
    else:
        print(f"  already carries the notebook instant on {event.event_id}")


async def seed(pan_id: str) -> int:
    await db.connect()
    database = db.get_db()
    service = FieldValidationService(database)
    existing = await service.list_events(pan_id=pan_id, limit=1000)
    seeded = [event for event in existing if event.name == EVENT_NAME]
    if seeded:
        # Never re-file the event over one that already exists, and never write the
        # old instant back. If a database still carries the superseded 19:30 IST
        # value from an earlier run of this script, move it onto the notebook
        # instant through the controlled resolution, which is the only sanctioned
        # path and which records what it displaced.
        for event in seeded:
            await _retire_superseded_rain_at(service, event)
        print(f"already seeded: {pan_id} already has “{EVENT_NAME}” (skipped).")
        return 0

    payload = RainfallEventCreate(
        pan_id=pan_id,
        name=EVENT_NAME,
        event_timestamp=EVENT_AT,
        # Left at the default on purpose: nothing establishes whether 23:30 IST
        # was the rain's start, its end, or the moment of logging.
        event_milestone="unknown",
        reported_by="field-operator",
        actual_rainfall_mm=90.0,
        rainfall_timestamp=RAIN_AT,
        # The notebook dates the rain at 23:30 IST. Naming it here means a freshly
        # seeded event is never born with an instant nobody can account for, and
        # never needs correcting later. The instant stays operator-reported: a
        # notebook records a time, it does not measure one.
        rainfall_source=RAIN_EVIDENCE,
        rainfall_timestamp_evidence=RAIN_EVIDENCE,
        rainfall_timestamp_verification="operator_reported",
        forecast_rainfall_mm=40.0,
        # No observation_id, observer or evidence_source: these are the times
        # the report claims, and the module refuses to let them look corroborated
        # until a stored record backs them.
        pre_event={
            "salinity_g_l": 250.0,
            "water_depth_cm": 20.0,
            "timestamp": datetime(2026, 3, 13, 8, 0, tzinfo=timezone.utc),
            "source": "manual",
        },
        post_event={
            "salinity_g_l": 20.0,
            "water_depth_cm": 50.0,
            "timestamp": datetime(2026, 3, 15, 8, 0, tzinfo=timezone.utc),
            "source": "manual",
        },
        interventions=[
            {
                "kind": "brine_transfer_out",
                "direction": "out",
                "performed_at": datetime(2026, 3, 14, 19, 0, tzinfo=timezone.utc),
                "description": (
                    "Brine transferred out after the event; the transferred "
                    "volume was not recorded."
                ),
            }
        ],
        notes=(
            "User-reported reference event for the field-validation dashboard. "
            "Seeded from a report, not from a stored field record: the snapshot "
            "instants, the rainfall instant and the milestone are unverified "
            "until a source document is linked."
        ),
    )
    event = await service.create_event(payload)
    comparison = await service.get_comparison(event.event_id)
    print(f"seeded event {event.event_id} for {pan_id}:")
    print(f"  actual rainfall          : {comparison.actual_rainfall_mm:.1f} mm")
    print(f"  predicted salinity       : {comparison.predicted_salinity_g_l:.3f} g/L")
    print(f"  reported salinity        : {comparison.reported_salinity_g_l} g/L")
    print(f"  |Δ| salinity             : {comparison.salinity_abs_diff_g_l:.3f} g/L")
    print(f"  predicted depth          : {comparison.predicted_depth_cm:.3f} cm")
    print(f"  reported depth           : {comparison.reported_depth_cm} cm")
    print(f"  |Δ| depth                : {comparison.depth_abs_diff_cm} cm")
    print(f"  intervention_affected    : {comparison.intervention_affected}")
    print(f"  unknown_intervention_vol : {comparison.unknown_intervention_volumes}")
    print(f"  independently_verified   : {comparison.independently_verified}")
    print(f"  verification_state       : {comparison.verification_state.value}")
    print(f"  event_milestone          : {comparison.event_milestone.value}")
    print(f"  event instant (UTC)      : {comparison.event_timestamp_utc}")
    print(f"  event instant (IST)      : {comparison.event_timestamp_ist}")
    print(f"  pre-event linked record  : {comparison.pre_event_observation_id or 'none'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pan-id", default="pan_orginal", help="Registered pan to attach the event to.")
    args = parser.parse_args()
    return asyncio.run(seed(args.pan_id))


if __name__ == "__main__":
    sys.exit(main())