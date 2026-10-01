"""Module 19 - the append-only ledger of completed research experiments.

The service owns the one thing that cannot be trusted to a client: the content
hash. It is computed server-side over a canonical encoding of everything the
record submitted, using the same canonical encoding the dataset builder hashes
with, and it is what makes re-posting an identical record return the stored
one rather than write a duplicate. ``created=false`` is not a lesser success;
it is the ledger saying that this exact experiment has already been recorded,
so another row would be an artefact of the retry rather than a reproduction.

Exports are built from the stored record, not from a second copy of it, so a
manifest and a CSV can never disagree with the ledger.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status

from app.db.collections import RESEARCH_EXPERIMENTS
from app.db.errors import DuplicateDocumentError
from app.db.repository import MongoRepository
from app.models.experiment import ResearchExperiment, build_experiment_id
from app.schemas.experiments import (
    ExperimentCreate,
    ExperimentListResponse,
    ExperimentManifest,
    ExperimentRead,
    ledger_csv_columns,
    metric_table_columns,
)
from app.services.csv_security import sanitize_prose_cell
from app.services.dataset_versioning import content_hash

#: How many times an identical-write race is replayed before giving up. The
#: unique index turns a concurrent collision into a bounded retry; beyond this
#: the only honest answer is a conflict.
MAX_WRITE_ATTEMPTS = 3

REUSED_REASON = (
    "An identical experiment is already recorded, so this exact run is one "
    "experiment; returning the stored record instead of duplicating it."
)

#: Fields the service assigns and that are therefore excluded from the content
#: hash. Everything a researcher submitted is in the hash; the record-keeping
#: is not, so retrying an identical submission cannot collide with a different
#: timestamp.
_EXCLUDED_FROM_HASH = ("_id", "experiment_id", "timestamp", "content_hash", "manifest_version")


@dataclass(frozen=True)
class CreateOutcome:
    """What a write to the ledger did."""

    experiment: ResearchExperiment
    created: bool
    reason: str | None


class ExperimentService:
    """Read and append to the research experiment ledger."""

    def __init__(self, database: Any) -> None:
        self._database = database

    def _repository(self) -> MongoRepository[ResearchExperiment]:
        return MongoRepository(
            self._database, RESEARCH_EXPERIMENTS, ResearchExperiment
        )

    # -- hashing -----------------------------------------------------------

    @staticmethod
    def _hash_of_submitted(draft: ExperimentCreate) -> str:
        """The identity of everything a researcher submitted.

        The create schema is exactly the set of hashed fields, so its JSON dump
        is the canonical payload. ``mode="json"`` turns dates into their ISO
        spelling and keeps floats bit-exact, which is what the dataset builder's
        canonical encoder expects and what makes the digest reproducible from a
        stored record itself.
        """
        return content_hash(draft.model_dump(mode="json"))

    @staticmethod
    def _hash_of_stored(experiment: ResearchExperiment) -> str:
        """Recompute an identity from a stored record, so a read can be verified.

        Used by ``manifest``-style callers to prove a stored record matches its
        hash without trusting the hash field. Equal to ``_hash_of_submitted`` for
        every record this service wrote, because nothing between the request and
        the store changes a submitted value.
        """
        payload = experiment.model_dump(by_alias=True, exclude_none=True, mode="json")
        for key in _EXCLUDED_FROM_HASH:
            payload.pop(key, None)
        return content_hash(payload)

    # -- write -------------------------------------------------------------

    async def create(self, draft: ExperimentCreate) -> CreateOutcome:
        """Record one experiment, or return the identical stored one.

        Append-only by construction: this is the only writing method, there is no
        update and no delete, and the unique content-hash index means no two rows
        can ever claim one experiment.
        """
        digest = self._hash_of_submitted(draft)
        repository = self._repository()

        existing = await repository.find({"content_hash": digest}, limit=1)
        if existing:
            return CreateOutcome(existing[0], created=False, reason=REUSED_REASON)

        experiment = ResearchExperiment(
            **draft.model_dump(),
            experiment_id=build_experiment_id(),
            content_hash=digest,
        )

        for _attempt in range(MAX_WRITE_ATTEMPTS):
            try:
                saved = await repository.insert(experiment)
            except DuplicateDocumentError:
                raced = await repository.find({"content_hash": digest}, limit=1)
                if raced:
                    return CreateOutcome(
                        raced[0], created=False, reason=REUSED_REASON
                    )
                continue
            return CreateOutcome(saved, created=True, reason=None)

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The identical experiment could not be recorded after several "
                "attempts; this is a storage race, not a content problem."
            ),
        )

    # -- reads -------------------------------------------------------------

    async def get(self, experiment_id: str) -> ResearchExperiment:
        """One experiment by its natural key."""
        return await self._repository().get("experiment_id", experiment_id)

    async def list_experiments(
        self,
        *,
        model: str | None = None,
        target: str | None = None,
        dataset_hash: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ExperimentListResponse:
        """The ledger, newest first, optionally narrowed to what a reader scans.

        ``dataset_hash`` is matched exactly because a content hash is an identity,
        not a search term; ``model`` and ``target`` are words a reader looks for
        and are matched loosely the way Mongo matches any string field.
        """
        query: dict[str, Any] = {}
        if model:
            query["model"] = model
        if target:
            query["target"] = target
        if dataset_hash:
            query["dataset_hash"] = dataset_hash

        repository = self._repository()
        total = await repository.count(query)
        experiments = await repository.find(
            query,
            sort=[("timestamp", -1)],
            skip=offset,
            limit=limit,
        )
        timestamps = [experiment.timestamp for experiment in experiments]
        return ExperimentListResponse(
            items=[ExperimentRead.from_experiment(item) for item in experiments],
            total=total,
            limit=limit,
            offset=offset,
            earliest_timestamp=min(timestamps) if timestamps else None,
            latest_timestamp=max(timestamps) if timestamps else None,
        )

    async def status_counts(self) -> dict[str, int | str | None]:
        """Totals for the module status endpoint, cheaply.

        Uniqueness comes from Mongo's ``distinct`` on the relevant fields, so a
        research team of ten never needs the whole ledger in memory to know how
        many experiments, models, datasets and targets it has run.
        """
        repository = self._repository()
        return {
            "experiments_total": await repository.count(),
            "distinct_models": len(await repository.distinct("model")),
            "distinct_datasets": len(await repository.distinct("dataset_hash")),
            "distinct_targets": len(await repository.distinct("target")),
            "oldest_recorded_at": (
                await self._oldest_timestamp(repository)
            ),
            "newest_recorded_at": (
                await self._newest_timestamp(repository)
            ),
        }

    async def _oldest_timestamp(self, repository: MongoRepository[ResearchExperiment]) -> str | None:
        oldest = await repository.find({}, sort=[("timestamp", 1)], limit=1)
        return oldest[0].timestamp.isoformat() if oldest else None

    async def _newest_timestamp(self, repository: MongoRepository[ResearchExperiment]) -> str | None:
        newest = await repository.find({}, sort=[("timestamp", -1)], limit=1)
        return newest[0].timestamp.isoformat() if newest else None

    # -- export ------------------------------------------------------------

    async def export_one(
        self, experiment: ResearchExperiment, output_format: str
    ) -> tuple[str, str, str]:
        """Return (media_type, body, filename) for one experiment's results.

        JSON is the reproducibility manifest, so the exported file is exactly
        what another researcher would need. CSV is the metrics table, one metric
        per row, for pasting into a table without re-keying it.
        """
        filename = experiment.experiment_id
        if output_format == "csv":
            buffer = io.StringIO()
            writer = csv.DictWriter(
                buffer, fieldnames=metric_table_columns(), extrasaction="ignore"
            )
            writer.writeheader()
            for name, value in experiment.metrics.items():
                writer.writerow({"metric": name, "value": value})
            return "text/csv; charset=utf-8", buffer.getvalue(), f"{filename}.csv"

        manifest = ExperimentManifest.from_experiment(experiment)
        body = json.dumps(
            manifest.model_dump(mode="json"), indent=2, sort_keys=True, default=str
        )
        return "application/json", body, f"{filename}.json"

    async def export_ledger(
        self,
        *,
        model: str | None = None,
        target: str | None = None,
        dataset_hash: str | None = None,
        limit: int = 1000,
        output_format: str,
    ) -> tuple[str, str, str]:
        """Return (media_type, body, filename) for a whole-ledger download.

        Every row is one experiment. Nested facts are encoded per row as JSON
        strings in the CSV form, so any reader can parse them back out; the JSON
        form carries the full records, keys sorted for a cleaner diff.
        """
        response = await self.list_experiments(
            model=model, target=target, dataset_hash=dataset_hash, limit=limit, offset=0
        )
        records = [item.model_dump(mode="json") for item in response.items]
        if output_format == "csv":
            buffer = io.StringIO()
            writer = csv.DictWriter(
                buffer, fieldnames=ledger_csv_columns(), extrasaction="ignore"
            )
            writer.writeheader()
            for record in records:
                writer.writerow(self._ledger_row(record))
            return (
                "text/csv; charset=utf-8",
                buffer.getvalue(),
                "experiments-ledger.csv",
            )
        body = json.dumps(records, indent=2, sort_keys=True, default=str)
        return "application/json", body, "experiments-ledger.json"

    @staticmethod
    def _ledger_row(record: dict[str, Any]) -> dict[str, Any]:
        """Flatten one record's nested facts into rectangular CSV fields."""
        row = dict(record)
        row["features"] = json.dumps(record.get("features") or [], separators=(",", ":"))
        row["hyperparameters"] = json.dumps(record.get("hyperparameters") or {}, separators=(",", ":"))
        row["metrics"] = json.dumps(record.get("metrics") or {}, separators=(",", ":"))
        row["software_versions"] = json.dumps(record.get("software_versions") or {}, separators=(",", ":"))
        for column in ("model", "notes"):
            if column in row:
                row[column] = sanitize_prose_cell(row[column])
        return row

    @staticmethod
    def manifest_payload(experiment: ResearchExperiment) -> dict[str, Any]:
        """The self-contained reproducibility manifest for one experiment.

        Built entirely from the immutable stored record, with the schema version,
        canonical form and algorithm named, so a reader can recompute the hash
        from the bytes and verify the record without trusting the stored field.
        """
        return ExperimentManifest.from_experiment(experiment).model_dump(
            mode="json"
        )