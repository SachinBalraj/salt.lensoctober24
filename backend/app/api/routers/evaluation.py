"""Module 9 - evaluate trained classifiers, and refuse when there is nothing to score.

The routes here are thin on purpose. The arithmetic is in :mod:`app.ml.evaluation`,
the resampling is in :mod:`app.ml.cross_validation`, the layout is in
:mod:`app.ml.comparison` and the orchestration is in
:class:`app.services.evaluation.EvaluationService`. What is left for the router is
the part that is genuinely about HTTP: turning a refusal into a status code a client
can branch on, and streaming the same readings out as JSON or CSV.

**Refusals are status codes carrying a machine-readable code.** ``409`` for a
dataset that has drifted or an artifact that is not on this host -- both mean
something about the stored records is not what the model version says it is, and
both are fixed by rebuilding or re-registering rather than by re-asking. ``404``
when the model or the report simply is not there. ``422`` for data that cannot
support the evaluation, or a request naming something that cannot be evaluated. The
body is the same shape in every case: a fixed ``detail`` sentence, a ``refusal``
code, and the counts that caused it. A client never has to match on prose.

**Nothing here computes a metric.** A missing metric is absent in the response, with
its reason, and absent in the export. The router has no branch anywhere that could
turn ``None`` into ``0.0`` -- the only place a number could acquire a default is a
table cell, and a cell is a rendering of a reading rather than a computation.

**The export is the same readings, not a second set.** ``GET .../export`` writes the
stored record verbatim as JSON, or flattens the comparison table to CSV with a
reason column beside every metric. Neither path re-derives, rounds, or fills
anything, so the downloaded file and the screen cannot disagree.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api.deps import get_db
from app.ml.comparison import SELECTION_CRITERION, build_comparison
from app.models.evaluation import EvaluationRefusal
from app.models.model_evaluation import ModelEvaluation
from app.schemas.evaluation import (
    CompareVersionsResponse,
    ComparisonResponse,
    EvaluationDetailResponse,
    EvaluationListResponse,
    ExportFormat,
    RefusalResponse,
    RunEvaluationRequestBody,
    to_detail,
    to_summary,
)
from app.services.evaluation import (
    EvaluationRefusalError,
    EvaluationService,
    export_csv,
    export_record,
)

router = APIRouter(prefix="/api/evaluation", tags=["09-evaluation"])

DESCRIPTION = (
    "Scores every trained classifier on the rows it was held out on -- accuracy, "
    "precision, recall, F1, ROC-AUC and PR-AUC where each is defined -- with "
    "per-class performance, confusion matrices, and a cross-validation design chosen "
    "to match the dataset's structure. Results are stored against the model version "
    "they describe and are downloadable for reproducibility."
)

#: Stated as a set of refusals rather than as a description, because the useful
#: question about an evaluation endpoint is what it will decline to do.
WHAT_THIS_MODULE_DOES_NOT_DO = (
    "This module scores models that were fitted and registered. It will not stand up "
    "a model to score, will not re-split a dataset that was split a particular way, "
    "will not substitute a working split for one that no longer divides the data, and "
    "will not report a metric it did not compute. An absent metric appears as absent "
    "with the reason attached, in the response, in the table, in the charts and in "
    "the download. No model is called best; the one marked selected is marked by a "
    "rule stated in the response beside it."
)

#: The two conditions that mean the stored records no longer match what the model
#: version says they are. Both are fixed by rebuilding or re-registering rather than
#: by asking again, which is what separates them from a 422.
_CONFLICT_REFUSALS = frozenset({"dataset_drift", "artifact_unavailable"})

#: Every metric is a reading: a value, a computed flag, a variant where the name is
#: ambiguous, and a reason where the metric is absent. Stated on the status endpoint
#: because it is the contract, and a client that has not read it will assume a
#: nullable float and fill the gap.
READING_CONTRACT = (
    "Every metric is a reading carrying value, computed, variant and reason, never a "
    "bare number and never a nullable one. value is null exactly when computed is "
    "false, and reason then says why. A metric that was not calculated is never "
    "rendered as 0.0, in the API, in the table, in a chart or in the download."
)


def get_evaluation_service() -> EvaluationService:
    """Build the service with a lazy database handle.

    Same shape as the other routers' providers: ``get_db`` is passed rather than
    called, so an unconfigured database raises its 503 inside the handler and a
    malformed request is still a 422 regardless of whether Mongo is reachable.
    """
    return EvaluationService(get_db)


ServiceDependency = Annotated[EvaluationService, Depends(get_evaluation_service)]


def _refusal_response(error: EvaluationRefusalError) -> JSONResponse:
    """Render a refusal with the status code its condition implies.

    404 when the model or the report is not there -- the caller asked about something
    that does not exist, which is a different situation from asking about something
    that cannot be evaluated. 409 for the two conditions that mean the stored records
    no longer match the model version. 422 for everything else: data that cannot
    support the evaluation, or a request that cannot be honoured.

    Decided from the refusal's code in one place, so a new code cannot arrive
    without choosing which of the three it belongs to rather than falling through to
    whichever branch happened to be written last.
    """
    if error.not_found:
        code = status.HTTP_404_NOT_FOUND
    elif error.refusal.refusal in _CONFLICT_REFUSALS:
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_422_UNPROCESSABLE_ENTITY
    return JSONResponse(
        status_code=code,
        content=RefusalResponse(**error.refusal.model_dump()).model_dump(mode="json"),
    )


@router.get("/", summary="Module 9 implementation status")
def module_status() -> dict[str, Any]:
    """What this module does, and what it refuses to do."""
    return {
        "module": "evaluation",
        "number": 9,
        "implemented": True,
        "description": DESCRIPTION,
        # False because nothing is evaluated until a caller names a model version to
        # score. Reporting otherwise would imply a report exists.
        "evaluated": False,
        "comparison_columns": [
            "model",
            "accuracy",
            "precision",
            "recall",
            "f1",
            "roc_auc",
            "pr_auc",
        ],
        "selection_rule": SELECTION_CRITERION,
        "metric_representation": READING_CONTRACT,
        "note": WHAT_THIS_MODULE_DOES_NOT_DO,
        "endpoints": {
            "run": "POST /api/evaluation/run",
            "read_one": "GET /api/evaluation/{model_id}/metrics",
            "compare_candidates": "GET /api/evaluation/compare",
            "compare_versions": "GET /api/evaluation/compare/versions",
            "list": "GET /api/evaluation/metrics",
            "export": "GET /api/evaluation/{model_id}/export",
        },
    }


@router.post(
    "/run",
    summary="Evaluate a registered model and store the report",
    response_model=None,
    responses={
        200: {
            "model": EvaluationDetailResponse,
            "description": (
                "The report was recomputed and replaced an existing one for this "
                "model version. 200 rather than 201, because the resource already "
                "existed: the run is deterministic given the recorded seed, so this "
                "is one measurement refreshed and not a second one."
            ),
        },
        201: {
            "model": EvaluationDetailResponse,
            "description": "The report was computed and stored against the model version.",
        },
        404: {
            "model": RefusalResponse,
            "description": "No model version is registered under that id.",
        },
        409: {
            "model": RefusalResponse,
            "description": (
                "The stored records no longer match the model version: the dataset "
                "has drifted, or the artifact is not on this host. Nothing was written."
            ),
        },
        422: {
            "model": RefusalResponse,
            "description": (
                "The model version cannot be evaluated from what it records, or the "
                "data cannot support the evaluation. Nothing was written."
            ),
        },
    },
)
async def run_evaluation(
    body: RunEvaluationRequestBody, service: ServiceDependency
) -> Response:
    """Score the model, cross-validate where the data allows, and store the report.

    Returns 201 with the full report, or 200 when an existing report was replaced.
    Returns 404, 409 or 422 with a refusal body when the model version cannot be
    evaluated, having written nothing at all.

    The response carries every candidate's evaluation, not only the selected model's,
    because the comparison is the deliverable: four candidates were fitted and the
    gap between them is the number that says whether the dataset is large enough for
    a model to generalise at all.
    """
    try:
        outcome = await service.evaluate(
            body.model_id,
            cross_validation_splits=body.cross_validation_splits,
        )
    except EvaluationRefusalError as exc:
        return _refusal_response(exc)
    except (ValueError, LookupError) as exc:
        # A split that cannot be rebuilt, a feature the catalogue does not know. A
        # caller error rather than a server one, so 422 naming the problem.
        return _refusal_response(
            EvaluationRefusalError(
                EvaluationRefusal(
                    detail=str(exc),
                    refusal="invalid_request",
                    model_id=body.model_id,
                    reasons=[str(exc)],
                )
            )
        )

    detail = to_detail(outcome.document, recomputed=outcome.recomputed)
    return JSONResponse(
        status_code=(
            status.HTTP_200_OK if outcome.recomputed else status.HTTP_201_CREATED
        ),
        content=detail.model_dump(mode="json"),
    )


@router.get(
    "/metrics",
    summary="List stored evaluations and the models that can be evaluated",
    response_model=EvaluationListResponse,
)
async def list_evaluations(
    service: ServiceDependency,
) -> EvaluationListResponse:
    """Every stored evaluation, newest first, plus every registered model.

    The two lists differ on purpose. ``evaluations`` is what has been scored;
    ``evaluable_models`` is what could be, including the models never evaluated. A
    picker built from the first would hide a trainable model behind a decision to
    score it, which is not a decision a client can make on the caller's behalf.
    """
    documents = await service.list_evaluations()
    models = await service.list_models()
    evaluated_ids = {document.model_id for document in documents}
    return EvaluationListResponse(
        evaluations=[to_summary(document) for document in documents],
        count=len(documents),
        evaluable_models=[
            {
                "model_id": record.model_id,
                "version": record.version,
                "target": record.target.value,
                "algorithm": record.algorithm,
                "status": record.status.value,
                "dataset_id": record.dataset_id,
                "trained_at": record.trained_at,
                "evaluated": record.model_id in evaluated_ids,
            }
            for record in models
        ],
    )


@router.get(
    "/compare/versions",
    summary="Compare stored evaluations across model versions",
    response_model=CompareVersionsResponse,
)
async def compare_versions(
    service: ServiceDependency,
) -> CompareVersionsResponse:
    """The same six columns, across model versions rather than across candidates.

    Answers "did the later model do better", which is the question a reader arrives
    with. It is only answerable when the rows match, so the response says whether
    they do: two versions fitted to different dataset revisions, or to different
    held-out fractions, are not comparable however good their numbers look beside
    each other, and a table that ranked them anyway would report the difference
    between the datasets as a difference between the models.

    The table is built by the same function that lays out the candidate comparison,
    with the versions keyed by ``model_id@version`` instead of by algorithm. One
    implementation means the two tables cannot come to disagree about what a missing
    cell means, what the selection criterion is, or which rows share a denominator.
    """
    documents = await service.list_evaluations()
    summaries = [to_summary(document) for document in documents]

    if not documents:
        return CompareVersionsResponse(
            evaluations=[],
            count=0,
            comparison=None,
            not_comparable=[
                (
                    "No evaluation has been stored yet, so there is nothing to "
                    "compare. Nothing is inferred from a model merely existing."
                )
            ],
        )

    sets = {summary.evaluation_set for summary in summaries}
    counts = {summary.evaluated_rows for summary in summaries}
    not_comparable: list[str] = []
    if len(sets) > 1:
        not_comparable.append(
            "These evaluations describe different row sets ("
            + "; ".join(sorted(sets))
            + "), so a difference between them may be a difference in the rows rather "
            "than in the models."
        )
    if len(counts) > 1:
        not_comparable.append(
            "These evaluations were computed on different numbers of rows, so their "
            "figures are not on a common denominator."
        )
    if len(documents) < 2:
        not_comparable.append(
            "Only one stored evaluation exists, so there is nothing to compare it "
            "against. One number is not a comparison."
        )

    comparison: ComparisonResponse | None = None
    if not not_comparable:
        by_version = {
            f"{document.model_id}@{document.version}": document.evaluation
            for document in documents
        }
        built = build_comparison(
            candidate_evaluations=by_version,
            # None, not "the best version". Nothing selects a winner across versions:
            # the rule that picks a candidate picks among algorithms fitted on one
            # dataset, and applying it across versions would compare figures from
            # different held-out sets under a criterion written for a single one.
            selected_algorithm=None,
        )
        built["selected_algorithm"] = None
        comparison = ComparisonResponse(**built)

    return CompareVersionsResponse(
        evaluations=summaries,
        count=len(summaries),
        comparison=comparison,
        not_comparable=not_comparable,
    )


@router.get(
    "/compare",
    summary="The comparison table for one model version",
    response_model=ComparisonResponse,
    responses={404: {"model": RefusalResponse, "description": "No stored evaluation."}},
)
async def compare_candidates(
    model_id: Annotated[str, Query(description="The registered model to compare.")],
    service: ServiceDependency,
) -> Response:
    """The six columns across the candidates trained for one model version.

    ``model_id`` rather than ``version``: the registry's own key is ``model_id`` and
    the version is on the record, so a caller holding an id is not also asked to know
    which version it resolved to. The version is in the response for the reader who
    does want to cite it.

    The stored table is returned as it was written, rather than rebuilt from the
    stored evaluations. The two are the same object today, and returning the stored
    one is what makes the download and the screen provably identical -- a rebuild
    could differ from the stored table after any future change to the layout rules,
    and the file a reader downloaded last month would then no longer match what they
    are shown now.
    """
    try:
        document = await service.get_evaluation(model_id)
    except EvaluationRefusalError as exc:
        return _refusal_response(exc)
    return JSONResponse(content=document.comparison)


@router.get(
    "/{model_id}/metrics",
    summary="Read one stored evaluation in full",
    response_model=EvaluationDetailResponse,
    responses={404: {"model": RefusalResponse, "description": "No stored evaluation."}},
)
async def read_evaluation(model_id: str, service: ServiceDependency) -> Response:
    """Every figure stored for one model version, with its provenance.

    The per-candidate evaluations and the per-fold evaluations are both included,
    because a headline number is checkable only against the row it was computed on,
    and the alternative is a reader taking it on trust.
    """
    try:
        document = await service.get_evaluation(model_id)
    except EvaluationRefusalError as exc:
        return _refusal_response(exc)
    return JSONResponse(content=to_detail(document).model_dump(mode="json"))


@router.get(
    "/{model_id}/export",
    summary="Download the experiment record for one model version",
    response_model=None,
    responses={
        200: {
            "description": (
                "The stored record as JSON, or the comparison table as CSV. Both are "
                "built from the same readings the API serves, so neither can contain a "
                "metric the other omitted."
            )
        },
        404: {"model": RefusalResponse, "description": "No stored evaluation."},
    },
)
async def export_evaluation(
    model_id: str,
    service: ServiceDependency,
    export_format: Annotated[
        ExportFormat,
        Query(
            alias="format",
            description="json for the full stored record, csv for the comparison table.",
        ),
    ] = "json",
) -> Response:
    """The machine-readable record, for a paper, a reviewer or a re-analysis.

    JSON is the complete stored report: provenance, every candidate, every fold, the
    comparison, and the caveats. CSV is the comparison table flattened, with a
    ``*_computed`` and a ``*_reason`` column beside every metric so an empty cell
    reads as "not calculated" rather than as zero.

    An absent metric is an empty cell in the CSV. Not ``0``, and not ``0.0``: a CSV
    is opened in a spreadsheet where a numeric column gets summed by a click, and a
    zero there would be aggregated into a headline the data does not support.
    """
    try:
        document: ModelEvaluation = await service.get_evaluation(model_id)
    except EvaluationRefusalError as exc:
        return _refusal_response(exc)

    stem = f"{document.model_id}_{document.version}_evaluation"
    if export_format == "csv":
        return PlainTextResponse(
            content=export_csv(document),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{stem}.csv"'},
        )
    return Response(
        content=json.dumps(export_record(document), indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{stem}.json"'},
    )


__all__ = [
    "DESCRIPTION",
    "READING_CONTRACT",
    "WHAT_THIS_MODULE_DOES_NOT_DO",
    "get_evaluation_service",
    "module_status",
    "router",
]
