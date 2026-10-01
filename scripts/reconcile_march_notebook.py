"""Reconcile the North Pan March event against the original field notebook.

The user-confirmed notebook record supplies four things this event did not have,
and this script attaches each one without touching anything else:

1. **A real pre-event reading.** The 250 g/L at 20 cm snapshot was filed from a
   report, so it carried no record behind it. The notebook entry is a genuine
   measurement taken 13 March 2026 at 13:30 IST, and it is filed as *its own*
   observation at that original instant -- ``2026-03-13T08:00:00Z``. It is a new
   record, not a moved one: the existing 30 September reading of the same pan
   with the same figures is a different measurement months later, is left exactly
   as stored, and is still refused for a March event (it postdates the rain).
2. **A link to that reading.** Through the pre-event link route the snapshot now
   takes its instant from a record that existed independently of this event, so
   its timestamp becomes corroborated. The event itself stays ``reported``.
3. **The milestone.** The notebook dates the rain's *start*, so
   ``event_milestone`` becomes ``rainfall_start`` -- stated from the record, not
   inferred from the rainfall timestamp, and the previous ``unknown`` stays
   readable in the append-only history.
4. **The post-event collection time.** The notebook dates that sampling at
   15 March 2026 13:30 IST, which is the instant already stored. It is restated
   against the notebook so the provenance is on the record, which deliberately
   does *not* corroborate it: an operator restating a time cannot corroborate
   itself, so ``timestamp_verified`` stays False.

**The rainfall instant is moved onto the notebook, by superseding the old one.** The
notebook puts the rain's start at 14 March 2026 23:30 IST
(``2026-03-14T18:00:00Z``); the event was filed carrying 19:30 IST
(``2026-03-14T14:00:00Z``) -- four hours earlier, and four hours *before* the event
instant it was filed against. Both accounts go on the record first, in both zones,
with the size of the disagreement, so the disagreement is auditable. Only then is
the notebook instant adopted through the controlled resolution, which requires the
caller to account for the value being displaced.

That resolution does not overwrite anything. The 19:30 IST value is retained as an
explicitly superseded entry whose meaning is left ``unknown`` and whose supporting
record is ``None``, alongside the seed-script literal that introduced it, and it is
rendered as history rather than as a second measurement. The event then reports
``reconciliation_state = "complete"``.

**The instant stays operator-reported.** A notebook records a time; it does not
measure one. ``rainfall_timestamp_verification`` is capped at ``operator_reported``,
``rainfall_timestamp_observed`` stays False, and ``independently_verified`` stays
False -- this path has no way to set any of them, and claiming a rain-gauge reading
when no gauge record exists is rejected outright.

**What is deliberately not written.** No transfer volume: the brine that was
moved out was never measured, so the intervention keeps its stored timestamp and
its unknown volume rather than being given a number. No observer name beyond what
the notebook states. No instrument, gauge or method detail. No measurement,
salinity, depth, rainfall figure or ``verification_state`` is altered, and
``independently_verified`` stays False -- nothing on this path can set it.

Every step is idempotent and keyed on what it already finds, so re-running adds
no duplicate observation, no duplicate correction, no duplicate conflict and no
second superseded entry. The script snapshots the September evidence before and
after and exits non-zero if any of it moved.

Run from the backend directory so the application settings (MONGODB_URI etc.)
are picked up from ``backend/.env``:

    backend/.venv/bin/python ../scripts/reconcile_march_notebook.py

Or use --dry-run to print what would change without writing.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# The backend package lives one directory up from this script; put it on the
# import path so the reconciliation can be run from anywhere, not only from
# inside backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.collections import (
    OPERATOR_DECISIONS,
    PAN_OBSERVATIONS,
    RECOMMENDATIONS,
    SIMULATION_RUNS,
)
from app.db.mongo import db
from app.schemas.field_validation import (
    EventCorrectionCreate,
    PreEventObservationLink,
    RainfallTimestampConflictCreate,
    RainfallTimestampResolutionCreate,
)
from app.schemas.observation import PanObservationCreate
from app.services.field_validation import FieldValidationService, resolve_timezone
from app.services.observations import ObservationService

#: The site's own zone, resolved by the same helper the service uses, so a local
#: time printed here can never disagree with the one the module publishes.
IST = resolve_timezone("IST")

EVENT_NAME = "North Pan 14 Mar 2026 rainfall event"
PAN_ID = "pan_orginal"

#: The pre-event measurement exactly as the original North Pan field notebook
#: records it. These are the only instants this script writes.
PRE_EVENT_AT = datetime(2026, 3, 13, 8, 0, tzinfo=timezone.utc)  # 13:30 IST
PRE_EVENT_AT_LOCAL = "2026-03-13T13:30"
RAINFALL_START_AT = datetime(2026, 3, 14, 18, 0, tzinfo=timezone.utc)  # 23:30 IST
POST_EVENT_AT = datetime(2026, 3, 15, 8, 0, tzinfo=timezone.utc)  # 13:30 IST

#: Where the notebook lives. Named in every provenance field so no figure in this
#: event can be read as if it came from the event report itself.
NOTEBOOK_SOURCE = "North Pan field notebook (manual field measurement)"
NOTEBOOK_ZONE = "IST"

#: The notebook names the person who took the reading, so the name is recorded.
#: Nothing is inferred for the readings it does not attribute.
NOTEBOOK_OBSERVER = "site operator"

MILESTONE_REASON = (
    "The original North Pan field notebook records the rain starting at "
    "14 March 2026, 23:30 IST. The milestone was previously unknown because no "
    "source document established what the event instant marked."
)
POST_EVENT_REASON = (
    "The notebook dates the post-event sampling at 15 March 2026, 13:30 IST, "
    "which is the instant this event already carries. Restating it records where "
    "the time came from; it does not corroborate the instant, because an "
    "operator restating a time cannot corroborate itself."
)
CONFLICT_REASON = (
    "The notebook dates the rain's start at 14 March 2026, 23:30 IST "
    "(2026-03-14T18:00:00Z). This event was filed with a rainfall timestamp of "
    "14 March 2026, 19:30 IST (2026-03-14T14:00:00Z), four hours earlier. Both "
    "accounts are kept on the record so the disagreement is auditable; which one "
    "is in force is settled by the resolution below."
)
RESOLUTION_REASON = (
    "No rain-gauge record supports 19:30 IST, and no source establishes what that "
    "time was meant to mark, so it cannot stand as an observed rainfall instant. "
    "The North Pan field notebook records the rain at 23:30 IST, which is the "
    "instant adopted here. The earlier value is retained as superseded audit "
    "history with its meaning left unknown; it is not a measurement."
)
#: Where the displaced instant came from, and the label it carried. Recorded so
#: an auditor can see that 19:30 IST entered the record as a seed-script literal
#: and was never backed by a gauge export.
SUPERSEDED_ORIGIN = (
    "scripts/seed_field_validation.py RAIN_AT literal (19:30 IST hardcoded in an "
    "earlier version of the seed script)"
)
SUPERSEDED_SOURCE_CLAIM = "manual rain gauge"
LINK_REASON = (
    "The original field record for the pre-event reading has been found: the "
    "North Pan field notebook entry of 13 March 2026, 13:30 IST. It is filed as "
    "its own observation at that instant and linked here."
)

#: Collections whose September content must be byte-identical afterwards.
SEPTEMBER_EVIDENCE = (
    PAN_OBSERVATIONS,
    SIMULATION_RUNS,
    RECOMMENDATIONS,
    OPERATOR_DECISIONS,
)


def _fingerprint(document: dict[str, Any]) -> str:
    """A stable digest of one stored document, for the before/after check.

    ``_id`` is excluded because it is a storage detail, not content; everything
    else is serialised in sorted key order so the digest is comparable.
    """
    content = {key: value for key, value in document.items() if key != "_id"}
    payload = json.dumps(content, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


async def september_fingerprints(database: Any) -> dict[str, list[str]]:
    """Digest every protected document, so "September did not move" is checked.

    All observations are included, not only the September one: filing a March
    record must add to this collection without altering what is already in it.
    """
    return {
        name: [
            f"{document.get('observation_id') or document.get('run_id') or document.get('recommendation_id') or document.get('decision_id') or '?'}:"
            f"{_fingerprint(document)}"
            for document in await database[name].find({}).to_list(None)
        ]
        for name in SEPTEMBER_EVIDENCE
    }


async def find_march_observation(service: ObservationService) -> Any | None:
    """The notebook's pre-event reading, if it has already been filed."""
    page = await service.list_observations(pan_id=PAN_ID, limit=1000)
    target = PRE_EVENT_AT.astimezone(timezone.utc)
    for observation in page.items:
        if observation.timestamp.astimezone(timezone.utc) == target:
            return observation
    return None


async def reconcile(dry_run: bool) -> int:
    await db.connect()
    database = db.get_db()
    field_service = FieldValidationService(database)
    observation_service = ObservationService(database)

    events = await field_service.list_events(pan_id=PAN_ID, limit=1000)
    matches = [event for event in events if event.name == EVENT_NAME]
    if not matches:
        print(
            f"No event named “{EVENT_NAME}” on pan {PAN_ID}; nothing to reconcile.",
            file=sys.stderr,
        )
        return 1
    if len(matches) > 1:
        print(
            f"{len(matches)} events named “{EVENT_NAME}” on pan {PAN_ID}; "
            "refusing to guess which one to reconcile.",
            file=sys.stderr,
        )
        return 1

    event = matches[0]
    before = await september_fingerprints(database)

    print(f"pan                 : {event.pan_id}")
    print(f"event_id            : {event.event_id}")
    print(f"event instant (UTC) : {event.event_timestamp.isoformat()}")
    print(f"stored rain (IST)   : {event.rainfall_timestamp.astimezone(IST).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"notebook rain (IST) : {RAINFALL_START_AT.astimezone(IST).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"milestone before    : {event.event_milestone.value}")
    print(f"pre-event link      : {event.pre_event.observation_id or 'none'}")
    print(f"open conflicts      : {len(event.unresolved_rainfall_timestamp_conflicts)}")
    print()

    # -- 1. the pre-event measurement, as its own record --------------------
    march = await find_march_observation(observation_service)
    if march is not None:
        print(f"pre-event reading   : already filed as {march.observation_id}")
    else:
        if dry_run:
            print("pre-event reading   : would file a new observation at "
                  f"{PRE_EVENT_AT.isoformat()} ({PRE_EVENT_AT_LOCAL} {NOTEBOOK_ZONE})")
        else:
            march = await observation_service.create(
                PanObservationCreate(
                    pan_id=PAN_ID,
                    timestamp=PRE_EVENT_AT,
                    salinity_g_l=250.0,
                    water_depth_cm=20.0,
                    data_source="manual",
                    observer=NOTEBOOK_OBSERVER,
                    evidence_source=NOTEBOOK_SOURCE,
                    note=(
                        "Pre-event brine for the 14 March 2026 rainfall event, as "
                        "recorded in the original North Pan field notebook."
                    ),
                )
            )
            print(f"pre-event reading   : filed as {march.observation_id}")

    # -- 2. link that reading to the event ---------------------------------
    if event.pre_event.observation_id is not None:
        print(f"link                : already linked to {event.pre_event.observation_id}")
    elif dry_run or march is None:
        print("link                : would link the notebook reading to the event")
    else:
        event = await field_service.link_pre_event_observation(
            event.event_id,
            PreEventObservationLink(
                observation_id=march.observation_id,
                measured_at_local=PRE_EVENT_AT_LOCAL,
                measured_timezone=NOTEBOOK_ZONE,
                observer=NOTEBOOK_OBSERVER,
                evidence_source=NOTEBOOK_SOURCE,
                reason=LINK_REASON,
                corrected_by="field notebook reconciliation",
            ),
        )
        print(f"link                : linked {march.observation_id} "
              f"(pre-event timestamp corroborated: {event.pre_event.timestamp_verified})")

    # -- 3. the milestone the notebook establishes --------------------------
    if event.event_milestone.value == "rainfall_start":
        print("milestone           : already rainfall_start")
    elif dry_run:
        print("milestone           : would set rainfall_start (from unknown)")
    else:
        event = await field_service.record_correction(
            event.event_id,
            EventCorrectionCreate(
                field="event_milestone",
                event_milestone="rainfall_start",
                reason=MILESTONE_REASON,
                evidence_source=NOTEBOOK_SOURCE,
                corrected_by="field notebook reconciliation",
            ),
        )
        print("milestone           : set to rainfall_start (previous value kept in history)")

    # -- 4. the post-event collection time ----------------------------------
    # Restating a timestamp records the evidence on the correction itself, not
    # on the snapshot, so the history is what says whether this has been done.
    already_restated = any(
        record.field == "post_event_timestamp"
        and record.evidence_source == NOTEBOOK_SOURCE
        for record in event.corrections
    )
    if already_restated:
        print("post-event time     : already recorded against the notebook")
    elif dry_run:
        print("post-event time     : would restate 2026-03-15 13:30 IST against the notebook")
    else:
        event = await field_service.record_correction(
            event.event_id,
            EventCorrectionCreate(
                field="post_event_timestamp",
                timestamp=POST_EVENT_AT,
                recorded_timezone=NOTEBOOK_ZONE,
                evidence_source=NOTEBOOK_SOURCE,
                reason=POST_EVENT_REASON,
                corrected_by="field notebook reconciliation",
            ),
        )
        print(
            "post-event time     : restated against the notebook "
            f"(timestamp_verified: {event.post_event.timestamp_verified})"
        )

    # -- 5. the rainfall instant, moved onto the notebook on evidence ------
    # The conflict below is still raised first, and deliberately left on the
    # record: the disagreement is what the resolution then answers, so removing
    # it would erase the reason the instant ever moved.
    already_conflicted = any(
        conflict.reported_timestamp.astimezone(timezone.utc)
        == RAINFALL_START_AT.astimezone(timezone.utc)
        for conflict in event.rainfall_timestamp_conflicts
    )
    if not already_conflicted:
        if dry_run:
            print("rainfall conflict   : would record both accounts and block the event")
        else:
            event = await field_service.record_rainfall_timestamp_conflict(
                event.event_id,
                RainfallTimestampConflictCreate(
                    reported_timestamp=RAINFALL_START_AT,
                    reported_source=NOTEBOOK_SOURCE,
                    reason=CONFLICT_REASON,
                    recorded_by="field notebook reconciliation",
                ),
            )
            conflict = event.rainfall_timestamp_conflicts[-1]
            print(
                "rainfall conflict   : recorded, both accounts kept "
                f"(stored {conflict.stored_timestamp.isoformat()} vs reported "
                f"{conflict.reported_timestamp.isoformat()}, {conflict.offset_seconds:+d} s)"
            )
    else:
        print("rainfall conflict   : already on the record")

    # With both accounts on file, the notebook instant is adopted through the
    # controlled route. The 19:30 IST literal the seed introduced is superseded
    # rather than overwritten, so it stays auditable without ever being rendered
    # as a second measurement. Re-running this is a no-op.
    if event.rainfall_timestamp.astimezone(timezone.utc) != RAINFALL_START_AT.astimezone(
        timezone.utc
    ):
        if dry_run:
            print("rainfall instant    : would supersede 19:30 IST with 23:30 IST")
        else:
            event = await field_service.resolve_rainfall_timestamp(
                event.event_id,
                RainfallTimestampResolutionCreate(
                    timestamp=RAINFALL_START_AT,
                    evidence_source=NOTEBOOK_SOURCE,
                    superseded_origin=SUPERSEDED_ORIGIN,
                    superseded_source_claim=SUPERSEDED_SOURCE_CLAIM,
                    superseded_supporting_record=None,
                    reason=RESOLUTION_REASON,
                    corrected_by="field notebook reconciliation",
                ),
            )
            displaced = event.superseded_rainfall_timestamps[-1]
            print(
                "rainfall instant    : notebook 23:30 IST now in force; superseded "
                f"{displaced.timestamp.isoformat()} kept as history, meaning "
                f"{displaced.meaning}, no supporting record"
            )
    else:
        print("rainfall instant    : already the notebook instant")

    # -- 6. the protected September evidence must be untouched ------------
    after = await september_fingerprints(database)
    added = len(after[PAN_OBSERVATIONS]) - len(before[PAN_OBSERVATIONS])
    preserved = all(
        after[name][: len(before[name])] == before[name] for name in SEPTEMBER_EVIDENCE
    )
    print()
    print(f"observations added  : {added} (expected 1, the March reading)")
    print(f"existing records unchanged: {preserved}")
    for name in SEPTEMBER_EVIDENCE:
        print(f"  {name}: {len(before[name])} -> {len(after[name])}")
    if not preserved or added > 1:
        print("  a previously stored record was altered; aborting.", file=sys.stderr)
        return 1

    # -- 7. the regenerated replay and its exports -------------------------
    comparison = await field_service.get_comparison(event.event_id)
    print()
    print("replay (rainfall-only, from the event's own starting brine):")
    print(f"  actual rainfall          : {comparison.actual_rainfall_mm} mm")
    print(f"  forecast rainfall        : {comparison.forecast_rainfall_mm} mm")
    print(f"  predicted salinity       : {comparison.predicted_salinity_g_l:.3f} g/L")
    print(f"  reported salinity        : {comparison.reported_salinity_g_l} g/L")
    print(f"  predicted depth          : {comparison.predicted_depth_cm:.3f} cm")
    print(f"  reported depth           : {comparison.reported_depth_cm} cm")
    print(f"  event_milestone          : {comparison.event_milestone.value}")
    print(f"  pre-event linked record  : {comparison.pre_event_observation_id or 'none'}")
    print(f"  pre-event corroborated   : {comparison.pre_event_timestamp_verified}")
    print(f"  post-event corroborated  : {comparison.post_event_timestamp_verified}")
    print(f"  rain instant (UTC)       : {comparison.rainfall_timestamp_utc}")
    print(f"  rain instant (IST)       : {comparison.rainfall_timestamp_ist}")
    print(f"  rain instant evidence    : {comparison.rainfall_timestamp_evidence}")
    print(f"  rain instant standing    : {comparison.rainfall_timestamp_verification.value}")
    print(
        "  rain instant observed    : "
        f"{comparison.rainfall_timestamp_observed}"
    )
    for displaced in comparison.superseded_rainfall_timestamps:
        print(
            f"  superseded rain instant  : {displaced.timestamp_utc} "
            f"({displaced.timestamp_ist}) - meaning {displaced.meaning}, "
            f"supporting record {displaced.supporting_record or 'none'}"
        )
    print(f"  unresolved conflicts     : {comparison.unresolved_conflict_count}")
    print(f"  intervention_affected    : {comparison.intervention_affected}")
    print(f"  unknown transfer volume  : {comparison.unknown_intervention_volumes}")
    print(f"  verification_state       : {comparison.verification_state.value}")
    print(f"  independently_verified   : {comparison.independently_verified}")
    print(f"  correction_count         : {len(event.corrections)}")
    print(f"  reconciliation_state     : {comparison.reconciliation_state}")

    if comparison.rainfall_timestamp_utc is None:
        print("  the stored rainfall instant is missing; check the event.", file=sys.stderr)
        return 1

    if not dry_run:
        exports = Path(__file__).resolve().parents[1] / "data" / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        for output_format, suffix in (("csv", "csv"), ("pdf", "pdf")):
            _, body, filename = await field_service.export(output_format, pan_id=PAN_ID)
            target = exports / filename
            target.write_bytes(body.encode("utf-8") if isinstance(body, str) else body)
            print(f"  wrote {target} ({target.stat().st_size} bytes)")

    if comparison.unresolved_conflict_count:
        print(
            "\nWARNING: a rainfall-timestamp conflict is still open, so the event "
            "cannot be called reconciled.",
            file=sys.stderr,
        )
        return 1
    if comparison.reconciliation_state != "complete":
        print(
            "\nWARNING: no conflict is open, but the event reports "
            f"{comparison.reconciliation_state!r} rather than 'complete'.",
            file=sys.stderr,
        )
        return 1
    print(
        "\nresolved on evidence: the notebook instant is in force, the 19:30 IST "
        "seed literal is retained as superseded history with its meaning unknown, "
        "and the instant remains operator-reported rather than instrument-verified."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without writing.",
    )
    args = parser.parse_args()
    return asyncio.run(reconcile(args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
