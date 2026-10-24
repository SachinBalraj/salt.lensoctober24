"""Module 21 - the multi-event experiment registry: read-only, and honest.

Where Module 19 records a research run the researcher declares, this reports what
the *field* produced. So it is read-only, and the read-only-ness is load-bearing
rather than a missing feature.

**No write route.** A prediction must be sealed before the observation that grades
it is recorded, and
:meth:`app.experiments.store.ExperimentStore.lock_prediction` refuses any write
that would change a sealed figure or that arrives after an observation is on the
record. An HTTP route that could set a prediction would provide a way around both
refusals while every field still looked correct, so there is no such route. Mutation
goes through the store, in an ordered workflow, by a process that can be audited.

**No filter parameter.** There is no ``?category=`` here. A filtered view of the
events is how a denominator gets quietly reduced: drop the intervention-affected
ones, aggregate the rest, and the arithmetic produces an accuracy figure with no
interventions anywhere in it. The endpoint returns every category's count, including
the zeros, so the denominator is visible without being selectable.

**No manifest built on request.** ``/manifest`` reads the committed
``VERSION.json``. A manifest assembled while serving the request would describe the
process answering it rather than the run that produced the results, and the gap
between those two is the only thing a manifest is for. When none is committed the
endpoint says so rather than building one.

The figures come from :mod:`app.experiments.metrics`, which withholds all of them
until the registry clears its declared sufficiency gate. That gate is not met
today, and the endpoints return that answer rather than a number.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict

from app.experiments.classifier import classify_event
from app.experiments.metrics import validate_events
from app.experiments.operator_study import DEFAULT_PROTOCOL, usability_report
from app.experiments.paths import ExperimentPaths
from app.experiments.protocols import (
    claim_requirements,
    evidence_gap_report,
    ml_readiness_report,
    operational_readiness_report,
)
from app.experiments.store import ExperimentStore

router = APIRouter(prefix="/api/experiment-registry", tags=["21-experiment-registry"])


def _store() -> ExperimentStore:
    """The registry, located from the environment at call time.

    Constructed per request rather than at import so that
    ``SALTLENS_EXPERIMENTS_DIR`` set after the app started still takes effect, and
    so a read-only process never creates the directory as a side effect of import.
    """
    return ExperimentStore(paths=ExperimentPaths())


def _protocol_responses() -> list[Any]:
    """Task responses, when any have been recorded.

    Empty today. The operator-study directory has no responses in it, so the
    usability report describes the absence rather than an empty sample being read
    as a zero completion rate.
    """
    store = _store()
    directory = store.paths.operator_study_records_dir
    if not directory.exists():
        return []
    from app.experiments.operator_study import TaskResponse

    responses = []
    for path in sorted(directory.glob("*.json")):
        responses.append(TaskResponse.model_validate_json(path.read_text(encoding="utf-8")))
    return responses


class EventRow(BaseModel):
    """One event in the API's shape.

    Includes the reasons, not just the category. An event list that showed only
    verdicts would let a reader accept or reject the classification without being
    able to see what it was based on.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str
    pan_id: str
    date: str
    category: str
    intervention_affected: bool
    included_in_metrics: bool
    independently_verified: bool
    rainfall_mm: float | None
    rainfall_is_forecast: bool | None
    rainfall_observed: bool | None
    predicted_salinity_g_l: float | None
    observed_salinity_g_l: float | None
    predicted_depth_cm: float | None
    observed_depth_cm: float | None
    reasons: list[str]
    evidence_gaps: list[str]


class EventsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_events: int
    #: Every category, including the ones at zero. A count that is absent reads as
    #: an oversight; a count of zero is a statement.
    counts_by_category: dict[str, int]
    events: list[EventRow]


@router.get("/manifest", summary="The frozen versions these results were computed under")
async def read_manifest() -> dict[str, Any]:
    """Git commit, app version, physics model, rule identity, schema versions.

    Reads ``VERSION.json`` rather than rebuilding the manifest, so the endpoint
    reports the environment the committed results were produced in rather than the
    one the API happens to be running in now. Those differ, and the difference is
    the interesting part.
    """
    store = _store()
    if not store.paths.manifest_file.exists():
        return {
            "available": False,
            "reason": (
                "No frozen manifest is present. Run "
                "scripts/seed_experiment_registry.py to write one. Nothing is "
                "returned in place of the manifest, because a manifest built at "
                "request time would describe this process rather than the run "
                "that produced the results."
            ),
        }
    import json

    return {
        "available": True,
        "manifest": json.loads(store.paths.manifest_file.read_text()),
    }


@router.get("/events", response_model=EventsResponse, summary="Every recorded event, with its classification")
async def read_events() -> EventsResponse:
    store = _store()
    events = store.list_events()

    rows: list[EventRow] = []
    counts: dict[str, int] = {}
    from app.experiments.models import EventClassification

    for category in EventClassification:
        counts[category.value] = 0

    for event in events:
        result = classify_event(event)
        counts[result.category.value] += 1
        rows.append(
            EventRow(
                event_id=event.event_id,
                pan_id=event.pan_id,
                date=event.date.isoformat(),
                category=result.category.value,
                intervention_affected=result.intervention_affected,
                included_in_metrics=result.is_accuracy_evidence,
                independently_verified=event.independent_verification is True,
                rainfall_mm=event.rainfall_mm,
                rainfall_is_forecast=event.rainfall_is_forecast,
                rainfall_observed=event.rainfall_observed,
                predicted_salinity_g_l=event.predicted_salinity_g_l,
                observed_salinity_g_l=event.observed_salinity_g_l,
                predicted_depth_cm=event.predicted_depth_cm,
                observed_depth_cm=event.observed_depth_cm,
                reasons=list(result.reasons),
                evidence_gaps=list(result.evidence_gaps),
            )
        )

    return EventsResponse(total_events=len(events), counts_by_category=counts, events=rows)


@router.get("/validation", summary="AE, MAE, RMSE and bias, or the reason none exist")
async def read_validation() -> dict[str, Any]:
    """The whole validation report.

    ``sufficient`` is ``false`` today and every figure comes back with
    ``computed: false``. That is the endpoint working correctly: it is refusing to
    produce an accuracy figure over one operator-reported event.
    """
    store = _store()
    report = validate_events(store.list_events())
    payload = report.as_dict()
    payload["export_csv"] = "experiments/events/events.csv"
    return payload


@router.get("/readiness", summary="What the ML and operational phases are waiting on")
async def read_readiness() -> dict[str, Any]:
    ml = ml_readiness_report()
    operational = operational_readiness_report()
    usability = usability_report(_protocol_responses())

    return {
        "ml": ml.as_dict(),
        "operational": operational.as_dict(),
        "usability": usability.as_dict(),
        "operator_study_protocol": DEFAULT_PROTOCOL.model_dump(mode="json"),
        "operational_claim_requirements": claim_requirements(),
    }


@router.get("/evidence-gaps", summary="Every gap, and the claims each one blocks")
async def read_evidence_gaps() -> dict[str, Any]:
    return {"gaps": evidence_gap_report()}


@router.get("/events.csv", summary="The registry as CSV", response_class=Response)
async def read_events_csv() -> Response:
    """The same rows as ``/events``, in the study's column order.

    ``Content-Disposition`` is set so a browser saves rather than renders it, and
    absent measurements are empty cells rather than zeros for the same reason they
    are in the file.
    """
    from app.experiments.store import write_events_csv

    store = _store()
    path = write_events_csv(store)
    return Response(
        content=path.read_text(encoding="utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="saltlens-experiment-events.csv"'},
    )


@router.get("/", summary="What this registry contains and what it does not yet support")
async def read_overview() -> dict[str, Any]:
    store = _store()
    events = store.list_events()
    report = validate_events(events)
    return {
        "registry_root": str(store.paths.root),
        "events_registered": len(events),
        "validation_sufficient": report.sufficient,
        "validation_message": report.message,
        "clean_verified_events": report.independently_verified_events,
        "min_clean_events_required": report.min_clean_events_required,
        "min_distinct_pans_required": report.min_distinct_pans_required,
        "phases": {
            "6_validation": report.sufficient,
            "8_ml": ml_readiness_report().status.value,
            "9_operational": operational_readiness_report().status.value,
        },
        "note": "This registry reports what was measured. It does not fill in what was not.",
    }


__all__ = ["EventRow", "EventsResponse", "router"]
