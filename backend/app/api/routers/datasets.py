"""Module 7 - research datasets built from stored observations and outcomes.

This is the only place in the project that turns measurements into rows a model
can be trained on, and the router is shaped around the five properties that make
those rows defensible.

**Nothing can be posted.** There is no field anywhere on this router that accepts
a number. A row exists because a pan observation exists at an anchor instant, and
its features are read from records that existed at or before that instant. A
client cannot introduce a value no instrument produced, which is what lets the
exported file be described as measurements rather than as assertions.

**The target is named, never assumed.** ``POST /`` requires a target from the
server's catalogue. The server then owns the label column, whether the target is
a regression or a classification, and whether it needs bin edges. Two callers
asking for the same target cannot produce differently shaped labels, so a score
reported against ``post_rain_salinity_g_l`` means one thing.

**The report arrives with the dataset.** Every build returns record count, date
range, pan count, per-column missingness with reasons, distributions, class
distribution, duplicates, data-source provenance and the leakage audit before
there is anything to train on. The report is stored with the manifest rather than
recomputed on read, because a report that shifts as readings arrive cannot be
cited as the state of the data at the time of an experiment.

**The hash is the identity, and a drifted build gets a new version.** Rows are
never stored; they are re-derived from current records, so an export cannot
quietly serve a stale snapshot. Every read that re-derives rows reports the
rebuilt hash beside the recorded one and says whether they still agree, and
``POST /{dataset_id}/rebuild`` stores a successor rather than overwriting the
predecessor, so the experiment that cited the old hash stays citable.

**Leakage is refused, not reported.** A build that breaks the causal ordering
raises inside the builder and becomes a 500 naming the offending rows. At that
point the correct response is to fix the builder, not to publish a dataset
nobody can defend.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.api.deps import OptionalDbDependency, get_db
from app.db.mongo import db
from app.schemas.dataset import (
    DEFAULT_PREVIEW_ROWS,
    DatasetBuildRequest,
    DatasetBuildResponse,
    DatasetCatalogResponse,
    DatasetListResponse,
    DatasetPreviewResponse,
    DatasetRebuildResponse,
    DatasetRebuildStatus,
    ResearchDatasetRead,
)
from app.services.dataset_builder import BuiltDataset
from app.services.dataset_export import preview_columns, summarise_rows
from app.services.datasets import (
    REBUILD_DRIFT_NOTE,
    REBUILD_STABLE_NOTE,
    REUSED_REASON,
    DatasetService,
    ExportResult,
    PreviewResult,
)

router = APIRouter(prefix="/api/datasets", tags=["07-datasets"])

DESCRIPTION = (
    "Research datasets assembled from stored pan observations, weather records and "
    "recorded outcomes: a configurable target, a documented feature set, a "
    "pre-training data report, a reproducible content hash and a CSV export."
)

ENDPOINTS = [
    "GET  /api/datasets/status",
    "GET  /api/datasets/catalog",
    "POST /api/datasets/",
    "GET  /api/datasets/?target=...",
    "GET  /api/datasets/{dataset_id}",
    "GET  /api/datasets/{dataset_id}/preview",
    "GET  /api/datasets/{dataset_id}/export",
    "POST /api/datasets/{dataset_id}/rebuild",
]

#: Header names carrying an export's two hashes. They are headers rather than body
#: fields because the body is the file, and a file with a comment prepended to it
#: is one a CSV reader will no longer read as a header row.
CONTENT_HASH_HEADER = "X-Content-Hash"
RECORDED_HASH_HEADER = "X-Recorded-Content-Hash"
DRIFT_HEADER = "X-Dataset-Drift"

DatasetIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="Server-assigned dataset identifier.",
        examples=["ds-3f9c1a04be7d2e58f61a0c93d4b5e672"],
    ),
]


def get_dataset_service() -> DatasetService:
    """Build the service with a lazy database handle.

    ``get_db`` is passed as a provider rather than called here so the 503 for an
    unconfigured database is raised inside the route handler, after FastAPI has
    validated the request body. A malformed build request is a 422 whether or not
    a database is reachable.
    """
    return DatasetService(get_db)


ServiceDependency = Annotated[DatasetService, Depends(get_dataset_service)]


def get_dataset_status_service(
    database: OptionalDbDependency,
) -> DatasetService | None:
    """Build a service for status reporting, or None when there is no database."""
    return DatasetService(database) if database is not None else None


OptionalStatusDependency = Annotated[
    DatasetService | None, Depends(get_dataset_status_service)
]


# -- status and catalogue ------------------------------------------------------


@router.get("/status", summary="Module 07 implementation status")
async def module_status(service: OptionalStatusDependency) -> dict[str, Any]:
    """Report that Module 07 is implemented, with live dataset and source totals.

    This endpoint never fails. An unavailable database is reported as
    ``datasets.available = false`` rather than raised, because reporting status is
    the one thing it exists to do.

    ``source_records`` sits beside the dataset count because a module that can
    build nothing and says how many observations it has to build from is more
    useful than one that reports zero datasets and leaves the reason unstated.
    """
    payload: dict[str, Any] = {
        "module": "datasets",
        "module_number": 7,
        "status": "implemented",
        "description": DESCRIPTION,
        "endpoints": ENDPOINTS,
    }
    if service is None:
        payload["datasets"] = {
            "available": False,
            "detail": db.unavailable_reason(),
        }
    else:
        payload["datasets"] = {
            "available": True,
            **await service.registry_counts(),
        }
    return payload


@router.get(
    "/catalog",
    response_model=DatasetCatalogResponse,
    summary="Every feature, every target, and every documented exclusion",
)
async def catalog(service: ServiceDependency) -> DatasetCatalogResponse:
    """Document the whole module without touching the database.

    Served from the catalogue rather than from stored data, so a researcher can
    read what would be built — and, just as importantly, what is deliberately not
    built — before there is a single observation to build it from.

    Every feature names the collection and field it was read from, the rule that
    selected the record, its unit, its leakage class and the reason it can be
    null. Every target names where its label is measured and how. The exclusions
    list the source fields left out with the reason for each, so their absence is
    a documented decision rather than a gap in the documentation.
    """
    return DatasetCatalogResponse(**service.catalog())


# -- building ------------------------------------------------------------------


@router.post(
    "/",
    response_model=DatasetBuildResponse,
    summary="Build a dataset from stored observations and outcomes",
)
async def build_dataset(
    payload: DatasetBuildRequest,
    response: Response,
    service: ServiceDependency,
) -> DatasetBuildResponse:
    """Assemble rows, report on them, hash them, and store the manifest.

    Returns 201 when a new manifest was written and 200 when an identical dataset
    already existed. The second case is not a lesser success: the same
    specification over the same stored records is the same dataset, and writing a
    second document would give one dataset two identifiers, so an experiment
    citing either would be citing something the records no longer describe.

    The response carries the manifest with its full report, so a caller sees the
    record count, date range, pan coverage, missing values, distributions, class
    distribution, duplicates, data sources and the leakage audit *before*
    training rather than after. It also carries the first rows, built from the
    same objects the CSV is built from, so what a researcher inspects in a browser
    and what lands in the file are the same numbers.

    ``content_hash`` is the dataset's identity and is what an experiment should
    cite. ``version`` counts the distinct datasets built for a target name in
    creation order and exists to make a history readable.

    Returns 422 for an unknown feature or target, a non-ascending bin edge, a
    horizon at or below zero, a banded target with no edges, or edges supplied to
    a target that has no use for them. A build that breaks the causal ordering
    becomes a 500 rather than a stored dataset, because at that point the correct
    response is to fix the builder rather than to publish the data.
    """
    outcome = await service.build(payload)
    response.status_code = (
        status.HTTP_201_CREATED if outcome.created else status.HTTP_200_OK
    )
    return _build_response(
        ResearchDatasetRead.from_dataset(outcome.dataset),
        created=outcome.created,
        built=outcome.built,
        preview_rows=payload.preview_rows,
    )


def _build_response(
    dataset: ResearchDatasetRead,
    *,
    created: bool,
    built: BuiltDataset,
    preview_rows: int,
) -> DatasetBuildResponse:
    """Attach a preview to a manifest.

    The rows come from :func:`summarise_rows`, the same helper the CSV uses, so a
    preview cannot describe a different dataset from the one that would be
    downloaded. ``reused_reason`` is attached only when nothing was written,
    because "why is this not a 201" has no answer worth returning otherwise.
    """
    return DatasetBuildResponse(
        dataset=dataset,
        created=created,
        reused_reason=None if created else REUSED_REASON,
        preview_columns=list(preview_columns(built)),
        preview_rows=summarise_rows(built, preview_rows),
    )


# -- reading -------------------------------------------------------------------


@router.get(
    "/",
    response_model=DatasetListResponse,
    summary="List stored dataset manifests, newest first",
)
async def list_datasets(
    service: ServiceDependency,
    target: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=64,
            description="Only datasets built for this research target.",
            examples=["post_rain_salinity_g_l"],
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Max manifests returned.")] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DatasetListResponse:
    """Return stored manifests with their reports, newest first.

    Each item is the full manifest including the report computed at build time.
    Reports are deliberately not recomputed on read: a report that changed as
    newer readings arrived could not be cited as the state of the data when an
    experiment ran.

    ``total`` is reported beside the page so a caller can tell that its window was
    truncated rather than assuming it is holding everything.
    """
    datasets, total = await service.list_datasets(
        target=target, limit=limit, offset=offset
    )
    return DatasetListResponse(
        items=[ResearchDatasetRead.from_dataset(dataset) for dataset in datasets],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{dataset_id}",
    response_model=ResearchDatasetRead,
    summary="Open one dataset manifest with its report",
)
async def get_dataset(
    dataset_id: DatasetIdPath,
    service: ServiceDependency,
) -> ResearchDatasetRead:
    """Return one manifest exactly as it was built.

    Declared after ``/status`` and ``/catalog`` so those literal paths are never
    captured as dataset identifiers.
    """
    return ResearchDatasetRead.from_dataset(await service.get(dataset_id))


@router.get(
    "/{dataset_id}/preview",
    response_model=DatasetPreviewResponse,
    summary="Inspect the first rows a dataset would export",
)
async def preview_dataset(
    dataset_id: DatasetIdPath,
    service: ServiceDependency,
    limit: Annotated[
        int,
        Query(
            ge=0,
            le=200,
            description="Max rows returned. Zero returns the columns and no rows.",
        ),
    ] = 10,
) -> DatasetPreviewResponse:
    """Return rows as they would be written to a file, in file order.

    The rows are re-derived from current records, so this also answers "does this
    dataset still describe the data" by way of ``drift``. ``content_hash`` is the
    digest of the rows rebuilt *now*; when it differs from the manifest's, the
    response says so rather than leaving the caller to compare two hashes it has
    to know to look for.

    ``total_rows`` is reported beside ``returned`` because the gap between them is
    the answer to "how much of this am I looking at", and leaving a caller to
    notice a 10-row sample for themselves is how ten rows get read as the dataset.

    An empty cell means the value was never recorded; zero means it was. The
    distinction survives CSV, which is why nothing in the builder writes a zero, a
    mean or a carry-forward into a gap.
    """
    return _preview_response(await service.preview(dataset_id, limit=limit))


def _preview_response(result: PreviewResult) -> DatasetPreviewResponse:
    """Shape a preview, attaching the drift verdict as a sentence."""
    return DatasetPreviewResponse(
        dataset_id=result.dataset.dataset_id,
        content_hash=result.content_hash,
        short_hash=result.dataset.short_hash,
        columns=list(preview_columns(result.built)),
        rows=result.rows,
        returned=len(result.rows),
        total_rows=result.built.report.record_count if result.built.report else 0,
        drift=result.drift,
        note=REBUILD_DRIFT_NOTE if result.drift else None,
    )


@router.get(
    "/{dataset_id}/export",
    response_class=Response,
    summary="Download the dataset as CSV",
    responses={
        200: {
            "description": "The dataset as CSV, with its provenance in the headers.",
            "content": {"text/csv": {}},
        }
    },
)
async def export_dataset(
    dataset_id: DatasetIdPath,
    service: ServiceDependency,
) -> Response:
    """Return the dataset as a CSV file whose provenance travels with it.

    The file is derived from the records stored at the moment of the request
    rather than from a snapshot, so it cannot quietly go stale. That has one
    consequence worth stating plainly: if an operator has backfilled a reading
    since the dataset was built, the file differs from the one the manifest's
    hash describes.

    So the response carries both hashes as headers — the hash of the rows in this
    file and the hash recorded on the manifest — plus a drift flag saying whether
    they still agree. A caller who exports twice, once before and once after a
    backfill, gets two different files and is told which one the manifest still
    matches, rather than being handed a file whose provenance they have to guess.

    The filename carries the target name and the short hash, so a file found on a
    desktop six months later still identifies its own provenance without being
    opened.
    """
    return _csv_response(await service.export(dataset_id))


def _csv_response(result: ExportResult) -> Response:
    """Wrap a CSV body with the headers that describe where it came from."""
    return Response(
        content=result.content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{result.filename}"',
            CONTENT_HASH_HEADER: result.content_hash,
            RECORDED_HASH_HEADER: result.recorded_content_hash,
            DRIFT_HEADER: "true" if result.drift else "false",
        },
    )


# -- reproducibility -----------------------------------------------------------


@router.post(
    "/{dataset_id}/rebuild",
    response_model=DatasetRebuildResponse,
    summary="Re-derive a dataset and check it still holds",
)
async def rebuild_dataset(
    dataset_id: DatasetIdPath,
    response: Response,
    service: ServiceDependency,
) -> DatasetRebuildResponse:
    """Re-derive the rows and report whether the stored hash still describes them.

    Returns 200 when the rebuild reproduced the stored content hash exactly: the
    dataset the caller already has is still the dataset the records describe, and
    that is the reproducibility claim made concrete.

    Returns 201 when it did not, because a reading or an outcome has been recorded
    or corrected since. That is not a failure, and the previous manifest is not
    overwritten — the successor is stored at the next version with
    ``supersedes_content_hash`` pointing back, so an experiment that cited the
    earlier hash stays citable against the data as it was at the time.

    The body is the resulting manifest, its report and a preview, so the caller
    can see what changed rather than only being told that something did.
    """
    outcome = await service.rebuild(dataset_id)
    response.status_code = (
        status.HTTP_200_OK if outcome.unchanged else status.HTTP_201_CREATED
    )
    return DatasetRebuildResponse(
        **_build_response(
            ResearchDatasetRead.from_dataset(outcome.dataset),
            created=not outcome.unchanged,
            built=outcome.built,
            preview_rows=DEFAULT_PREVIEW_ROWS,
        ).model_dump(),
        rebuild=DatasetRebuildStatus(
            unchanged=outcome.unchanged,
            reason=REBUILD_STABLE_NOTE if outcome.unchanged else REBUILD_DRIFT_NOTE,
            previous_content_hash=(
                outcome.previous.content_hash if outcome.previous else None
            ),
            previous_dataset_id=(
                outcome.previous.dataset_id if outcome.previous else None
            ),
        ),
    )
