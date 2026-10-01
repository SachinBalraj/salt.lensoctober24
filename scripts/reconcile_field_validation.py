"""Record the Module 20 audit outcome on the North Pan event, non-destructively.

The read-only audit of the North Pan field-validation event found:

* the event instant is correct — ``2026-03-14T18:00:00Z`` is 14 March 2026, 23:30
  IST — and is left exactly as stored;
* nothing establishes *what* that instant marks, so the milestone is filed as
  ``unknown`` rather than inferred from the rainfall timestamp four hours earlier;
* the pre-event snapshot (250 g/L at 20 cm) has no linked stored observation, and
  the only stored observation of that pan with those figures is dated 30 September,
  long after the event — so it cannot be this event's starting brine;
* the post-event timestamp has no corroborating record, and the intervention's
  transferred volume was never recorded.

This script writes exactly two things to the one event, both additive:

1. ``event_milestone = "unknown"``, so the stored document says what the audit
   concluded rather than relying on a default;
2. one entry in the event's append-only correction history recording the finding.

It does not touch the reported salinities, depths, rainfall, intervention,
``verification_state``, ``independently_verified``, or any other pan's records. It
prints the counts of the September records before and after so that "nothing else
moved" is checked rather than asserted, and it refuses to run twice for the same
finding, so re-running adds no second audit entry.

Run from the backend directory so the application settings (MONGODB_URI etc.)
are picked up from ``backend/.env``:

    backend/.venv/bin/python ../scripts/reconcile_field_validation.py

Or use --dry-run to print what would change without writing.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# The backend package lives one directory up from this script; put it on the
# import path so the reconciliation can be run from anywhere, not only from inside
# backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.collections import (
    OPERATOR_DECISIONS,
    PAN_OBSERVATIONS,
    RECOMMENDATIONS,
    SIMULATION_RUNS,
)
from app.db.mongo import db
from app.schemas.field_validation import EventCorrectionCreate
from app.services.field_validation import FieldValidationService

EVENT_NAME = "North Pan 14 Mar 2026 rainfall event"
PAN_ID = "pan_orginal"

#: The audit finding recorded in the event's history. Keyed on this exact string
#: so the script is idempotent: a second run recognises its own entry.
AUDIT_REASON = (
    "Provenance audit: the event instant is correct (2026-03-14T18:00:00Z = "
    "23:30 IST) but nothing establishes whether it marks the rain's start, its "
    "end, or the moment of logging, so the milestone is recorded as unknown. "
    "The pre-event snapshot (250 g/L at 20 cm) has no linked stored observation; "
    "the only stored observation of this pan with those figures is dated "
    "2026-09-30, after the event, and cannot be its starting brine. The "
    "post-event instant remains uncorroborated and the intervention volume "
    "remains unknown. No measurement, timestamp or flag was changed by this "
    "entry."
)
CORRECTION_FIELD = "event_milestone"

#: Collections that must be byte-for-byte untouched by this reconciliation.
SEPTEMBER_EVIDENCE = (
    PAN_OBSERVATIONS,
    SIMULATION_RUNS,
    RECOMMENDATIONS,
    OPERATOR_DECISIONS,
)


async def counts(database: object) -> dict[str, int]:
    """Document count per protected collection, for the before/after check."""
    return {
        name: await database[name].count_documents({})
        for name in SEPTEMBER_EVIDENCE
    }


async def reconcile(pan_id: str, dry_run: bool) -> int:
    await db.connect()
    database = db.get_db()
    service = FieldValidationService(database)

    events = await service.list_events(pan_id=pan_id, limit=1000)
    matches = [event for event in events if event.name == EVENT_NAME]
    if not matches:
        print(
            f"No event named “{EVENT_NAME}” on pan {pan_id}; nothing to reconcile.",
            file=sys.stderr,
        )
        return 1
    if len(matches) > 1:
        print(
            f"{len(matches)} events named “{EVENT_NAME}” on pan {pan_id}; "
            "refusing to guess which one to reconcile.",
            file=sys.stderr,
        )
        return 1

    event = matches[0]
    before_counts = await counts(database)
    print(f"pan            : {event.pan_id}")
    print(f"event_id       : {event.event_id}")
    print(f"event instant  : {event.event_timestamp.isoformat()}")
    print(f"milestone      : {event.event_milestone.value}")
    print(f"pre-event link : {event.pre_event.observation_id or 'none'}")
    print(f"post-event ok  : {event.post_event.timestamp_verified}")
    print(f"verified       : {event.verification_state.value}")
    print(f"corrections    : {len(event.corrections)}")

    if any(
        record.reason == AUDIT_REASON for record in event.corrections
    ):
        print("already recorded: the audit finding is in this event's history; skipping.")
        return 0

    if dry_run:
        print("dry run: would record the audit finding as one correction entry.")
        return 0

    updated = await service.record_correction(
        event.event_id,
        EventCorrectionCreate(
            field=CORRECTION_FIELD,
            reason=AUDIT_REASON,
            event_milestone="unknown",
            corrected_by="provenance audit",
            evidence_source=(
                "Read-only audit of the rainfall_events, pan_observations, "
                "simulation_runs, recommendations and operator_decisions "
                "collections; no source field record has yet been supplied."
            ),
        ),
    )

    after_counts = await counts(database)
    print(f"milestone      : {updated.event_milestone.value}")
    print(f"corrections    : {len(updated.corrections)}")
    print(f"verified       : {updated.verification_state.value}")
    print(f"pre-event link : {updated.pre_event.observation_id or 'none'}")
    print(f"protected counts unchanged: {before_counts == after_counts}")
    if before_counts != after_counts:
        print(
            f"  before: {before_counts}\n  after:  {after_counts}",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pan-id", default=PAN_ID, help="Pan carrying the audited event."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be recorded without writing.",
    )
    args = parser.parse_args()
    return asyncio.run(reconcile(args.pan_id, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())