"""Module 07 - the research dataset module.

A dataset here is a **claim**, and this service is what makes it checkable. It
assembles rows out of stored observations, weather and outcomes, reports on what
it assembled before anyone trains on it, and gives the result an identity that can
be re-derived.

Five properties are load-bearing.

**Only stored measurements go in.** Every row is anchored at a pan observation
that exists, every weather figure comes from a record that existed at that
instant, and every label comes from a measurement taken after it. This service
has no path that writes a value into a row from anywhere else, and the API has no
field with which a client could try.

**The target is configured, never inferred.** A build names one of the server's
targets and supplies a horizon. There is no default, because a label nobody chose
is a label nobody can defend, and a dataset with a plausible default label is the
easiest way to produce a result that means nothing.

**The report comes before the training.** Every build returns record counts, the
date range, pan coverage, per-column missingness with reasons, distributions,
the class distribution, duplicate groups, data-source provenance and the causal
audit, together with a list of the things a researcher should decide about. The
report is stored with the dataset rather than recomputed on demand, because a
report that changes as readings arrive cannot be cited as the state of the data
at the time of an experiment.

**The rows are not stored.** A snapshot would go stale the moment an operator
backfilled a reading, and a stale snapshot is worse than none: it looks
reproducible and is not. The manifest is stored; the rows are derived. The
content hash ties the two together, and the rebuild endpoint re-derives them and
says whether they still agree.

**A build that fails the causal audit is refused, not flagged.** A leaked dataset
will produce a score nobody can defend, and a warning attached to it is a warning
that gets ignored. The builder raises; this service turns that into a 500 naming
the offending rows, because at that point the correct response is to fix the
builder rather than to publish the data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any

from fastapi import HTTPException, status

from app.db.collections import (
    ACTUAL_OUTCOMES,
    PAN_OBSERVATIONS,
    RESEARCH_DATASETS,
    SALT_PANS,
    WEATHER_OBSERVATIONS,
)
from app.db.errors import DocumentNotFoundError, DuplicateDocumentError
from app.db.repository import MongoRepository
from app.models.actual_outcome import ActualOutcome
from app.models.base import normalize_pan_id
from app.models.dataset import (
    BUILDER_SCHEMA_VERSION,
    FeatureDefinitionRecord,
    ResearchDataset,
    TargetSpec,
)
from app.models.enums import ResearchTarget
from app.models.pan_observation import PanObservation
from app.models.risk_definition import RiskClassDistribution, RiskDefinition
from app.models.salt_pan import SaltPan
from app.models.weather import WeatherObservation
from app.schemas.dataset import DatasetBuildRequest, DatasetTargetRequest
from app.schemas.risk import RiskDistributionRequest
from app.services.dataset_builder import (
    LEAKAGE_CHECKS,
    LEAKAGE_POLICY,
    BuildSpec,
    BuildSpecError,
    BuiltDataset,
    DatasetBuilder,
    LeakageError,
    excluded_field_records,
)
from app.services.dataset_catalog import (
    DEFAULT_LOOKBACK_HOURS,
    DEFAULT_MAX_SOURCE_RECORDS,
    FEATURE_CATALOG,
    MIN_HORIZON_HOURS,
    ROW_KEY_NAMES,
    ROW_KEYS,
    TARGET_CATALOG,
    TARGET_INDEX,
    TargetDefinition,
    feature_names,
)
from app.services.dataset_export import (
    export_filename,
    summarise_rows,
    to_csv_bytes,
)
from app.services.dataset_report import build_report
from app.services.dataset_versioning import content_hash, hash_inputs, short_hash
from app.services.mass_balance import MassBalanceService
from app.services.risk_catalog import RETROSPECTIVE_VARIABLES
from app.services.risk_definitions import RiskDefinitionService
from app.services.risk_evaluation import build_class_distribution

#: Why an existing dataset was returned instead of a second one being written.
REUSED_REASON = (
    "An identical dataset already exists: the same specification over the same "
    "stored records produced the same content hash, so this is that dataset "
    "rather than a second copy of it. Rebuild it to check whether the records "
    "have moved on since."
)

NO_DATASET_ID = "No research dataset has the ID “{dataset_id}”."

LEAKAGE_DETAIL = (
    "The build produced rows that break the causal ordering, so no dataset was "
    "stored. This is a defect in the builder rather than in the data, and the "
    "rows named below must not be used: {violations}. The full audit is on the "
    "exception the API logged."
)

REBUILD_DRIFT_NOTE = (
    "Rebuilding now produced a different content hash, so a reading or an outcome "
    "has been recorded or corrected since this dataset was built. The rows below "
    "are current; the stored manifest describes what was true at build time."
)

REBUILD_STABLE_NOTE = (
    "Rebuilding now reproduced the stored content hash exactly, so this dataset is "
    "still what the stored records describe."
)


@dataclass(frozen=True)
class BuildOutcome:
    """One build, and whether it wrote anything.

    ``dataset`` and ``built`` travel together because a caller usually needs the
    rows it just hashed: a preview, a CSV, or a look at why a column is empty.
    """

    dataset: ResearchDataset
    built: BuiltDataset
    created: bool


@dataclass(frozen=True)
class ExportResult:
    """A CSV file, plus everything a caller needs to know about its provenance."""

    content: bytes
    filename: str
    content_hash: str
    recorded_content_hash: str
    drift: bool


@dataclass(frozen=True)
class MaterializationResult:
    """Every row of a stored dataset, rebuilt now, with drift reported.

    Both hashes are present for the same reason as in :class:`ExportResult`: the
    rows are derived from current records while the manifest is a statement about
    build time, and a caller deciding whether to train needs both numbers to make
    that decision rather than a bare flag.
    """

    dataset: ResearchDataset
    built: BuiltDataset
    content_hash: str
    recorded_content_hash: str
    drift: bool


@dataclass(frozen=True)
class PreviewResult:
    """The first rows of a dataset, and whether the manifest still describes them.

    ``content_hash`` is the digest of the rows rebuilt *now*, which is not
    ``dataset.content_hash`` when ``drift`` is set. Both are reachable from here
    so a caller can be told what changed as well as that something did; a bare
    boolean would leave nothing to compare against.
    """

    dataset: ResearchDataset
    built: BuiltDataset
    content_hash: str
    rows: list[dict[str, Any]]
    drift: bool


@dataclass(frozen=True)
class RebuildOutcome:
    """What a rebuild found, and the rows it found it in.

    The rebuilt rows travel with the verdict deliberately. A router that had to
    derive them a second time to render a preview would be reporting a hash from
    one derivation beside rows from another, and the two could disagree for
    reasons having nothing to do with the data.
    """

    dataset: ResearchDataset
    built: BuiltDataset
    content_hash: str
    unchanged: bool
    previous: ResearchDataset | None


class DatasetService:
    """Build, report, version and export research datasets."""

    def __init__(
        self,
        database: Any,
        builder: DatasetBuilder | None = None,
        mass_balance: MassBalanceService | None = None,
    ) -> None:
        self._database = database
        self._builder = builder or DatasetBuilder(mass_balance or MassBalanceService())
        self._resolved_pans: MongoRepository[SaltPan] | None = None
        self._resolved_observations: MongoRepository[PanObservation] | None = None
        self._resolved_weather: MongoRepository[WeatherObservation] | None = None
        self._resolved_outcomes: MongoRepository[ActualOutcome] | None = None
        self._resolved_datasets: MongoRepository[ResearchDataset] | None = None
        self._resolved_risk: RiskDefinitionService | None = None

    @property
    def _pan_repository(self) -> MongoRepository[SaltPan]:
        if self._resolved_pans is None:
            self._resolved_pans = MongoRepository(self._database, SALT_PANS, SaltPan)
        return self._resolved_pans

    @property
    def _observation_repository(self) -> MongoRepository[PanObservation]:
        if self._resolved_observations is None:
            self._resolved_observations = MongoRepository(
                self._database, PAN_OBSERVATIONS, PanObservation
            )
        return self._resolved_observations

    @property
    def _weather_repository(self) -> MongoRepository[WeatherObservation]:
        if self._resolved_weather is None:
            self._resolved_weather = MongoRepository(
                self._database, WEATHER_OBSERVATIONS, WeatherObservation
            )
        return self._resolved_weather

    @property
    def _outcome_repository(self) -> MongoRepository[ActualOutcome]:
        if self._resolved_outcomes is None:
            self._resolved_outcomes = MongoRepository(
                self._database, ACTUAL_OUTCOMES, ActualOutcome
            )
        return self._resolved_outcomes

    @property
    def _dataset_repository(self) -> MongoRepository[ResearchDataset]:
        if self._resolved_datasets is None:
            self._resolved_datasets = MongoRepository(
                self._database, RESEARCH_DATASETS, ResearchDataset
            )
        return self._resolved_datasets

    def _risk_definition_service(self) -> RiskDefinitionService:
        if self._resolved_risk is None:
            self._resolved_risk = RiskDefinitionService(self._database)
        return self._resolved_risk

    # -- specification ------------------------------------------------------

    async def _target_spec(
        self, payload: DatasetBuildRequest
    ) -> tuple[TargetSpec, RiskDefinition | None]:
        """Turn a request's target into a server-owned specification.

        The label column, the kind and the requirement for bin edges all come
        from the catalogue rather than the request, so two callers asking for the
        same target cannot produce differently shaped labels and two experiments
        cannot claim the same target means different things.

        Returns the specification and the rule it will be labelled with, together
        rather than separately, so the rule that was checked against the request
        is provably the rule that labels the rows. Re-reading it at the call site
        would leave a window in which a definition could be revised between the
        check and the build.
        """
        requested = payload.target
        try:
            definition = TARGET_INDEX[requested.name]
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Unknown research target “{requested.name.value}”. Available "
                    "targets: "
                    + ", ".join(target.name.value for target in TARGET_CATALOG)
                ),
            ) from exc

        if requested.horizon_hours < MIN_HORIZON_HOURS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"horizon_hours must be at least {MIN_HORIZON_HOURS}. A "
                    "measurement taken at the anchor instant describes the state "
                    "being predicted rather than its consequence, so a zero horizon "
                    "would label each row with the reading it is meant to forecast."
                ),
            )
        if definition.requires_bin_edges and not requested.bin_edges:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Target “{definition.name.value}” is banded, so bin_edges are "
                    "required. The module will not choose where the class "
                    "boundaries are: the class distribution would then be a fact "
                    "about that choice rather than about the pans."
                ),
            )
        if requested.bin_edges and not definition.requires_bin_edges:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Target “{definition.name.value}” is not banded, so bin_edges "
                    "have no meaning for it."
                ),
            )
        risk = await self._resolve_risk_definition(requested, definition)
        return TargetSpec(
            name=definition.name,
            label_column=definition.label_column,
            kind=definition.kind,
            horizon_hours=requested.horizon_hours,
            require_rain_event=requested.require_rain_event,
            bin_edges=requested.bin_edges,
            require_bin_edges=definition.requires_bin_edges,
            requires_risk_definition=definition.requires_risk_definition,
            risk_definition_id=requested.risk_definition_id,
            risk_definition_version=risk.version if risk else None,
            risk_specification_hash=risk.specification_hash if risk else None,
            allow_retrospective_label=requested.allow_retrospective_label,
        ), risk

    async def _resolve_risk_definition(
        self, requested: DatasetTargetRequest, definition: TargetDefinition
    ) -> RiskDefinition | None:
        """Fetch and check the rule a derived target will be labelled with.

        Two refusals here, both deliberate. A derived target with no rule is
        refused, because the alternative is a severity the module invented. A
        retrospective rule is refused unless the request acknowledged it, because
        a label that looks forecastable will be read as a forecast, and the audit
        is the last place that can be caught.
        """
        if not definition.requires_risk_definition:
            if requested.risk_definition_id is not None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"Target “{definition.name.value}” is not derived from a "
                        "rule, so risk_definition_id has no meaning for it."
                    ),
                )
            if requested.allow_retrospective_label:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"Target “{definition.name.value}” is measured rather than "
                        "derived, so its labels cannot be retrospective."
                    ),
                )
            return None

        if not requested.risk_definition_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Target “{definition.name.value}” is derived from a "
                    "researcher's rule, so risk_definition_id is required. The "
                    "module supplies no severity vocabulary and no threshold, and a "
                    "dataset built without a rule would have a class distribution "
                    "that is a fact about this module rather than about the pans. "
                    f"Write one first: POST /api/risk/definitions/."
                ),
            )

        risk = await self._risk_definition_service().get(requested.risk_definition_id)
        if risk.is_retrospective and not requested.allow_retrospective_label:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Risk definition {risk.definition_id!r} reads "
                    + ", ".join(
                        variable
                        for variable in risk.variables_used
                        if variable in RETROSPECTIVE_VARIABLES
                    )
                    + ", which is measured after the anchor, so its labels describe "
                    "what happened rather than what was knowable. Set "
                    "allow_retrospective_label to true to build a dataset from it "
                    "anyway; the dataset will be labelled retrospective throughout "
                    "and cannot be used to forecast the event it is graded against."
                ),
            )
        return risk

    @staticmethod
    def _build_spec(
        payload: DatasetBuildRequest,
        target: TargetSpec,
        risk: RiskDefinition | None = None,
    ) -> BuildSpec:
        """Assemble the builder's specification from a validated request.

        The rule arrives whole from :meth:`_target_spec` rather than being
        re-fetched, for the reason given there.
        """
        selected = (
            tuple(payload.features)
            if payload.features is not None
            else feature_names()
        )
        return BuildSpec(
            target=target,
            feature_names=selected,
            lookback_hours=payload.lookback_hours,
            pan_ids=tuple(payload.pan_ids or ()),
            start=payload.start,
            end=payload.end,
            include_unlabelled=payload.include_unlabelled,
            max_source_records=payload.max_source_records,
            risk_definition=risk,
        )

    # -- building -----------------------------------------------------------

    async def _assemble(self, spec: BuildSpec) -> tuple[BuiltDataset, str]:
        """Build the rows, report on them, and hash them.

        The three steps are inseparable: the report is computed from the rows and
        the hash from the rows, so a dataset's description and its identity always
        refer to the same data.
        """
        try:
            built = await self._builder.build(
                spec=spec,
                observations=self._observation_repository,
                weather=self._weather_repository,
                outcomes=self._outcome_repository,
                pans=self._pan_repository,
            )
        except LeakageError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=LEAKAGE_DETAIL.format(
                    violations="; ".join(
                        f"{violation.row_id}/{violation.field}: {violation.reason}"
                        for violation in exc.audit.violations[:5]
                    )
                ),
            ) from exc
        except BuildSpecError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

        built.report = build_report(built)
        digest = content_hash(
            hash_inputs(
                builder_schema_version=BUILDER_SCHEMA_VERSION,
                specification=spec.as_hashed_specification(),
                rows=built.canonical_rows(),
            )
        )
        return built, digest

    def _manifest(
        self,
        *,
        built: BuiltDataset,
        digest: str,
        name: str | None,
        version: int,
        supersedes: str | None,
    ) -> ResearchDataset:
        """Wrap a build as a storable manifest.

        The catalogue entry for each built feature is copied in verbatim, so the
        manifest describes its own columns without the reader having to hold the
        catalogue in their head.
        """
        return ResearchDataset(
            name=name,
            target=built.spec.target,
            content_hash=digest,
            builder_schema_version=BUILDER_SCHEMA_VERSION,
            version=version,
            feature_names=list(built.spec.feature_names),
            features=[
                FeatureDefinitionRecord(**asdict(feature)) for feature in built.features
            ],
            row_keys=list(ROW_KEY_NAMES),
            excluded_fields=list(built.audit.excluded_fields),
            lookback_hours=built.spec.lookback_hours,
            include_unlabelled=built.spec.include_unlabelled,
            max_source_records=built.spec.max_source_records,
            pan_filter=list(built.spec.pan_ids),
            window={
                "start": built.spec.start,
                "end": built.spec.end,
            },
            report=built.report,
            supersedes_content_hash=supersedes,
        )

    async def _find_by_hash(self, digest: str) -> ResearchDataset | None:
        matches = await self._dataset_repository.find(
            {"content_hash": digest}, limit=1
        )
        return matches[0] if matches else None

    async def _predecessor_for(self, target_name: str) -> ResearchDataset | None:
        """The newest dataset built for the same target, if there is one."""
        matches = await self._dataset_repository.find(
            {"target.name": target_name}, sort=[("version", -1)], limit=1
        )
        return matches[0] if matches else None

    async def build(self, payload: DatasetBuildRequest) -> BuildOutcome:
        """Build a dataset from stored records, or return the one that already is.

        Returns 200 with ``created = false`` when an identical dataset exists.
        Writing a second document with the same content would make two
        ``dataset_id`` values for one dataset, and an experiment citing either of
        them would be citing something the records no longer describe.

        The version number counts the distinct datasets built for a target name in
        creation order. It is a convenience for reading a history; the content
        hash is the identity, and it is the hash an experiment should cite.
        """
        target, risk = await self._target_spec(payload)
        spec = self._build_spec(payload, target, risk)
        built, digest = await self._assemble(spec)

        existing = await self._find_by_hash(digest)
        if existing is not None:
            return BuildOutcome(dataset=existing, built=built, created=False)

        predecessor = await self._predecessor_for(target.name.value)
        manifest = self._manifest(
            built=built,
            digest=digest,
            name=payload.name,
            version=(predecessor.version + 1) if predecessor else 1,
            supersedes=predecessor.content_hash if predecessor else None,
        )
        try:
            stored = await self._dataset_repository.insert(manifest)
        except DuplicateDocumentError:
            # A concurrent build of the same specification won the race. Its
            # document is this dataset, so return it rather than failing.
            raced = await self._find_by_hash(digest)
            if raced is None:  # pragma: no cover - the index says otherwise
                raise
            return BuildOutcome(dataset=raced, built=built, created=False)
        return BuildOutcome(dataset=stored, built=built, created=True)

    # -- reads --------------------------------------------------------------

    async def risk_distribution(
        self, definition: RiskDefinition, request: RiskDistributionRequest
    ) -> tuple[BuiltDataset, RiskClassDistribution]:
        """Derive a rule's labels over the stored records and report their shape.

        The rows are built by the ordinary builder with an operational-risk target
        pointing at this definition, so the labels reported here are the same
        objects a dataset built from the same definition would carry, with the same
        per-row provenance and the same audit. A separate evaluation path for
        "just show me the distribution" would be a second implementation of the
        rule, and the two would eventually disagree.

        Nothing is stored and nothing is fitted. The rows exist only for the length
        of the request, which is what makes this safe to call before deciding
        whether the rule is worth building a dataset from at all.
        """
        target_definition = TARGET_INDEX[ResearchTarget.OPERATIONAL_RISK]
        target = TargetSpec(
            name=target_definition.name,
            label_column=definition.label_column,
            kind=target_definition.kind,
            horizon_hours=request.horizon_hours,
            require_rain_event=request.require_rain_event,
            requires_risk_definition=True,
            risk_definition_id=definition.definition_id,
            risk_definition_version=definition.version,
            risk_specification_hash=definition.specification_hash,
            allow_retrospective_label=definition.is_retrospective,
        )
        spec = BuildSpec(
            target=target,
            # The whole feature catalogue, because a rule may legitimately read
            # any catalogued variable and a distribution that silently omitted
            # the one the rule depends on would report the rule as never matching.
            feature_names=feature_names(),
            lookback_hours=request.lookback_hours,
            pan_ids=request.pan_id_tuple,
            start=request.start,
            end=request.end,
            include_unlabelled=True,
            max_source_records=request.max_source_records,
            risk_definition=definition,
        )
        built, _ = await self._assemble(spec)
        evaluations = [row.risk for row in built.rows if row.risk is not None]
        return built, build_class_distribution(definition, evaluations)

    async def get(self, dataset_id: str) -> ResearchDataset:
        try:
            return await self._dataset_repository.get("dataset_id", dataset_id)
        except DocumentNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=NO_DATASET_ID.format(dataset_id=dataset_id),
            ) from exc

    async def list_datasets(
        self,
        *,
        target: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ResearchDataset], int]:
        """List stored manifests, newest first.

        The ``total`` is reported beside the page so a caller can tell that its
        window was truncated rather than assuming it is holding everything.
        """
        query: dict[str, Any] = {}
        if target:
            normalized = normalize_pan_id(target)
            known = {item.name.value for item in TARGET_CATALOG}
            if normalized not in known:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"Unknown research target “{target}”. Available targets: "
                        + ", ".join(sorted(known))
                    ),
                )
            query["target.name"] = normalized
        total = await self._dataset_repository.count(query)
        datasets = await self._dataset_repository.find(
            query, sort=[("created_at", -1)], limit=limit, skip=offset
        )
        return datasets, total

    async def preview(self, dataset_id: str, *, limit: int) -> PreviewResult:
        """Return the first rows of a dataset as they would be written to a file.

        The rows are rebuilt from current records, so the call also answers "does
        this dataset still describe the data" by way of the drift flag.
        """
        dataset = await self.get(dataset_id)
        spec = self.spec_from_manifest(dataset, await self._risk_for(dataset))
        built, digest = await self._assemble(spec)
        return PreviewResult(
            dataset=dataset,
            built=built,
            content_hash=digest,
            rows=summarise_rows(built, limit),
            drift=digest != dataset.content_hash,
        )

    async def export(self, dataset_id: str) -> ExportResult:
        """Return the CSV for a dataset, with both hashes attached.

        Both hashes are returned because the file is derived from current records
        while the manifest is a statement about build time. A caller who exports
        twice, once before and once after a backfill, gets two different files and
        is told which one the manifest still matches.
        """
        dataset = await self.get(dataset_id)
        spec = self.spec_from_manifest(dataset, await self._risk_for(dataset))
        built, digest = await self._assemble(spec)
        return ExportResult(
            content=to_csv_bytes(built),
            filename=export_filename(built, short_hash(digest)),
            content_hash=digest,
            recorded_content_hash=dataset.content_hash,
            drift=digest != dataset.content_hash,
        )

    async def materialize(self, dataset_id: str) -> MaterializationResult:
        """Re-derive every row of a stored dataset, and say whether it has drifted.

        ``preview`` returns rows too, but it is a display method: it summarises
        them to a limit and a caller has to know to reach into ``.built`` for the
        rest. Model training needs a named, complete, verified materialization,
        so it gets one rather than a preview-shaped back door.

        Drift is reported rather than raised. A dataset whose records have changed
        can still be read, and it is the caller's decision what to do about it --
        train against the manifest, rebuild first, or refuse. Pretending the two
        are the same thing would let a model be registered against a version
        number whose rows it was not actually fitted to.
        """
        dataset = await self.get(dataset_id)
        spec = self.spec_from_manifest(dataset, await self._risk_for(dataset))
        built, digest = await self._assemble(spec)
        return MaterializationResult(
            dataset=dataset,
            built=built,
            content_hash=digest,
            recorded_content_hash=dataset.content_hash,
            drift=digest != dataset.content_hash,
        )

    @staticmethod
    def spec_from_manifest(
        dataset: ResearchDataset, risk: RiskDefinition | None = None
    ) -> BuildSpec:
        """Reconstruct the specification a stored dataset was built from.

        Public because a rebuild, an export and a preview all have to derive their
        rows from the manifest rather than from anything the caller remembered, and
        because an experiment reproduced outside the API needs the same
        specification.

        ``risk`` is the rule the caller reloaded for a derived target. It is a
        parameter rather than looked up here because this method is synchronous and
        the rule lives in Mongo; :meth:`_risk_for` does the loading and the
        checking. Passing it in keeps the specification and the rule that labels
        the rows together, the same way :meth:`_target_spec` returns them together.
        """
        window = dataset.window or {}
        return BuildSpec(
            target=dataset.target,
            feature_names=tuple(dataset.feature_names),
            lookback_hours=dataset.lookback_hours,
            pan_ids=tuple(dataset.pan_filter),
            start=_as_datetime(window.get("start")),
            end=_as_datetime(window.get("end")),
            include_unlabelled=dataset.include_unlabelled,
            max_source_records=dataset.max_source_records,
            risk_definition=risk,
        )

    async def prediction_row(
        self, dataset: ResearchDataset, *, pan_id: str, as_of: datetime
    ) -> tuple[BuiltDataset, RiskDefinition | None]:
        """Build the single feature row an operational prediction needs.

        The specification comes from the stored manifest rather than from the
        caller, so the features a model is asked about at prediction time are built
        from the same target, feature list, lookback window and rule that produced
        its training rows. Handing this method a lookback or a feature list from the
        request would let a prediction be made on inputs assembled differently from
        the ones the model was fitted on, and the resulting number would still look
        like a risk score.

        The window is narrowed to one pan and one anchor instant, and unlabelled
        rows are included: an operational prediction is anchored at a moment whose
        outcome has not happened, which is precisely the case the builder's labelling
        pass cannot produce a label for.

        Returns the built rows and the rule that would label them, or ``None`` for a
        measured target. The rule is returned rather than applied so the caller can
        name the definition the prediction is expressed in, which a stored
        prediction record needs and a bare label does not carry.
        """
        risk = await self._risk_for(dataset)
        spec = self.spec_from_manifest(dataset, risk)
        narrowed = replace(
            spec,
            pan_ids=(pan_id,),
            start=as_of,
            end=as_of,
            include_unlabelled=True,
        )
        built, _ = await self._assemble(narrowed)
        return built, risk

    async def risk_definition_for(self, dataset: ResearchDataset) -> RiskDefinition | None:
        """The rule a stored dataset was built with, checked against the manifest.

        A public name for :meth:`_risk_for`, so a caller that needs the rule
        without rebuilding the dataset -- operational prediction, which reads its
        features from the twin rather than from a dataset -- can reach it. The
        checks are the same ones; only the caller differs.
        """
        return await self._risk_for(dataset)

    async def _risk_for(self, dataset: ResearchDataset) -> RiskDefinition | None:
        """Reload the rule a stored dataset was built with, and check it is the same.

        A manifest names its rule by id *and* by specification hash, and both are
        checked here. Without the check, a rebuild would silently re-label the rows
        with a different revision of the rule and report the result as a new
        dataset: the experiment would still be citable, but against a rule nobody
        chose. With it, a manifest that cites a rule which no longer resolves to
        that rule is refused rather than quietly reinterpreted.

        Returns ``None`` for a measured target, which is the only case in which a
        dataset carries no rule.
        """
        definition_id = dataset.target.risk_definition_id
        if definition_id is None:
            return None
        if not dataset.target.requires_risk_definition:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Dataset {dataset.dataset_id!r} names a risk definition but its "
                    "target is measured rather than derived, so the reference has no "
                    "meaning for it."
                ),
            )
        risk = await self._risk_definition_service().get(definition_id)
        expected = dataset.target.risk_specification_hash
        if expected is not None and risk.specification_hash != expected:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Dataset {dataset.dataset_id!r} records the specification of risk "
                    f"definition {risk.definition_id!r} version {risk.version} as "
                    f"{expected}, but that rule now hashes to "
                    f"{risk.specification_hash}. Definitions are immutable, so this "
                    "means the stored manifest and the stored rule no longer agree "
                    "and the dataset cannot be reproduced as described."
                ),
            )
        return risk

    # -- reproducibility ----------------------------------------------------

    async def rebuild(self, dataset_id: str) -> RebuildOutcome:
        """Re-derive a dataset and say whether it still holds.

        An unchanged verdict is the reproducibility claim made concrete: the
        stored records still describe the dataset the experiment cited. A changed
        verdict is not a failure — it means a reading or an outcome has been
        recorded since, and the new dataset is stored as the next version rather
        than replacing the old one, so the earlier experiment stays citable.
        """
        previous = await self.get(dataset_id)
        spec = self.spec_from_manifest(previous, await self._risk_for(previous))
        built, digest = await self._assemble(spec)
        if digest == previous.content_hash:
            return RebuildOutcome(
                dataset=previous,
                built=built,
                content_hash=digest,
                unchanged=True,
                previous=None,
            )

        successor = self._manifest(
            built=built,
            digest=digest,
            name=previous.name,
            version=previous.version + 1,
            supersedes=previous.content_hash,
        )
        try:
            stored = await self._dataset_repository.insert(successor)
        except DuplicateDocumentError:
            raced = await self._find_by_hash(digest)
            if raced is None:  # pragma: no cover - the index says otherwise
                raise
            stored = raced
        return RebuildOutcome(
            dataset=stored,
            built=built,
            content_hash=digest,
            unchanged=False,
            previous=previous,
        )

    # -- catalogue ----------------------------------------------------------

    @staticmethod
    def catalog() -> dict[str, Any]:
        """Everything this module can build, and everything it refuses to build.

        Served without touching the database, which is the point: a researcher
        should be able to read the feature set, the target set, the causal policy
        and the documented exclusions before there is a single observation to
        read them from. A catalogue that only appeared once data existed would
        arrive too late to inform the decision it exists to inform.

        The exclusions are taken from the same records the audit carries, so the
        documentation published here and the audit stored on a dataset can never
        describe different decisions.
        """
        return {
            "builder_schema_version": BUILDER_SCHEMA_VERSION,
            "leakage_policy": LEAKAGE_POLICY,
            "leakage_checks": list(LEAKAGE_CHECKS),
            "row_keys": [
                {
                    "name": key.name,
                    "source": key.source,
                    "description": key.description,
                }
                for key in ROW_KEYS
            ],
            "features": [
                {
                    "name": feature.name,
                    "kind": feature.kind.value,
                    "leakage_class": feature.leakage_class.value,
                    "unit": feature.unit,
                    "source_collection": feature.source_collection,
                    "source_field": feature.source_field,
                    "selection_rule": feature.selection_rule,
                    "derivation": feature.derivation,
                    "scientific_basis": feature.scientific_basis,
                    "nullable_reason": feature.nullable_reason,
                }
                for feature in FEATURE_CATALOG
            ],
            "targets": [
                {
                    "name": target.name,
                    "kind": target.kind.value,
                    "label_column": target.label_column,
                    "unit": target.unit,
                    "source_collection": target.source_collection,
                    "source_field": target.source_field,
                    "description": target.description,
                    "measurement_rule": target.measurement_rule,
                    "requires_bin_edges": target.requires_bin_edges,
                    "classes": list(target.classes) if target.classes else None,
                }
                for target in TARGET_CATALOG
            ],
            "excluded_fields": [
                record.model_dump() for record in excluded_field_records()
            ],
            "defaults": {
                "lookback_hours": DEFAULT_LOOKBACK_HOURS,
                "max_source_records": DEFAULT_MAX_SOURCE_RECORDS,
                "min_horizon_hours": MIN_HORIZON_HOURS,
                "include_unlabelled": True,
            },
        }

    # -- status -------------------------------------------------------------

    async def registry_counts(self) -> dict[str, Any]:
        """Totals for the status endpoint, plus the shape of the source data.

        Reports the source collections alongside the dataset count, because a
        module that can build nothing and says so is more useful than one that
        reports zero datasets and leaves the reason unstated.
        """
        return {
            "total_datasets": await self._dataset_repository.count(),
            "source_records": {
                SALT_PANS: await self._pan_repository.count(),
                PAN_OBSERVATIONS: await self._observation_repository.count(),
                WEATHER_OBSERVATIONS: await self._weather_repository.count(),
                ACTUAL_OUTCOMES: await self._outcome_repository.count(),
            },
            "buildable_targets": [target.name.value for target in TARGET_CATALOG],
        }


def _as_datetime(value: Any) -> datetime | None:
    """Read a window boundary back from a stored manifest.

    A manifest read from Mongo carries datetimes; one reconstructed in a test from
    a plain dict may carry ISO strings. Both are accepted so a rebuild does not
    depend on how the document happened to be deserialised.
    """
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"cannot read a window boundary from {value!r}")


__all__ = [
    "REBUILD_DRIFT_NOTE",
    "REBUILD_STABLE_NOTE",
    "REUSED_REASON",
    "BuildOutcome",
    "DatasetService",
    "ExportResult",
    "PreviewResult",
    "RebuildOutcome",
]
