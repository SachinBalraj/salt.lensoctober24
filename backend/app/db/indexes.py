from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pymongo
from pymongo.errors import PyMongoError

from app.db.collections import (
    ACTUAL_OUTCOMES,
    DECISION_RULES,
    DIGITAL_TWIN_STATES,
    EXPLANATIONS,
    ML_PREDICTIONS,
    MODEL_DEPLOYMENTS,
    MODEL_EVALUATIONS,
    MODEL_VERSIONS,
    OPERATOR_DECISIONS,
    PAN_OBSERVATIONS,
    RAINFALL_EVENTS,
    RECOMMENDATIONS,
    RESEARCH_DATASETS,
    RESEARCH_EXPERIMENTS,
    RISK_DEFINITIONS,
    SALT_PANS,
    SIMULATION_RUNS,
    USERS,
    WEATHER_OBSERVATIONS,
)
from app.db.errors import IndexProvisioningError
from app.db.resolution import resolve_database
from app.models import (
    ActualOutcome,
    DecisionRule,
    DigitalTwinState,
    Explanation,
    MLPrediction,
    ModelDeployment,
    ModelEvaluation,
    ModelVersion,
    OperatorDecision,
    PanObservation,
    RainfallEvent,
    Recommendation,
    ResearchDataset,
    ResearchExperiment,
    RiskDefinition,
    SaltPan,
    SimulationRun,
    User,
    WeatherObservation,
)

logger = logging.getLogger("saltlens.db.indexes")

#: Re-exported from :mod:`app.db.collections` so the many modules that reach for
#: a collection name alongside the index plan keep a single import site.
__all__ = [
    "ACTUAL_OUTCOMES",
    "COLLECTION_MODELS",
    "DECISION_RULES",
    "DIGITAL_TWIN_STATES",
    "EXPLANATIONS",
    "INDEX_PLAN",
    "ML_PREDICTIONS",
    "MODEL_DEPLOYMENTS",
    "MODEL_EVALUATIONS",
    "MODEL_VERSIONS",
    "OPERATOR_DECISIONS",
    "PAN_OBSERVATIONS",
    "RAINFALL_EVENTS",
    "RECOMMENDATIONS",
    "RESEARCH_DATASETS",
    "RESEARCH_EXPERIMENTS",
    "RISK_DEFINITIONS",
    "SALT_PANS",
    "SIMULATION_RUNS",
    "USERS",
    "WEATHER_OBSERVATIONS",
    "IndexFailure",
    "IndexReport",
    "IndexSpec",
    "ensure_indexes",
    "registry_collections",
    "resolves_to_field",
    "validate_index_plan",
]


@dataclass(frozen=True)
class IndexSpec:
    collection: str
    keys: tuple[tuple[str, int], ...]
    unique: bool = False
    sparse: bool = False
    name: str = ""
    #: A Mongo partial filter expression. Only documents matching it are indexed,
    #: which for a unique index means uniqueness is enforced *within* the
    #: filtered subset rather than across the whole collection. Used to state
    #: "at most one deployed model per target" in the database rather than only
    #: in service code that a concurrent pair of deployments could both miss.
    partial_filter: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.keys:
            raise ValueError("an index must contain at least one key")
        if not self.name:
            direction = "_".join("asc" if value > 0 else "desc" for _, value in self.keys)
            fields = "_".join(field for field, _ in self.keys)
            object.__setattr__(self, "name", f"{self.collection}_{fields}_{direction}")


@dataclass(frozen=True)
class IndexFailure:
    collection: str
    name: str
    error: str


@dataclass(frozen=True)
class IndexReport:
    created: tuple[str, ...] = ()
    failures: tuple[IndexFailure, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.failures

    def __bool__(self) -> bool:
        return self.ready

    def __iter__(self) -> Iterator[str]:
        return iter(self.created)

    def __len__(self) -> int:
        return len(self.created)

    def __getitem__(self, index: int) -> str:
        return self.created[index]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (list, tuple)):
            return list(self.created) == list(other)
        if isinstance(other, IndexReport):
            return self.created == other.created and self.failures == other.failures
        return NotImplemented


COLLECTION_MODELS: dict[str, type] = {
    USERS: User,
    SALT_PANS: SaltPan,
    PAN_OBSERVATIONS: PanObservation,
    WEATHER_OBSERVATIONS: WeatherObservation,
    DIGITAL_TWIN_STATES: DigitalTwinState,
    SIMULATION_RUNS: SimulationRun,
    ML_PREDICTIONS: MLPrediction,
    EXPLANATIONS: Explanation,
    RECOMMENDATIONS: Recommendation,
    DECISION_RULES: DecisionRule,
    OPERATOR_DECISIONS: OperatorDecision,
    ACTUAL_OUTCOMES: ActualOutcome,
    MODEL_VERSIONS: ModelVersion,
    RESEARCH_DATASETS: ResearchDataset,
    RESEARCH_EXPERIMENTS: ResearchExperiment,
    RISK_DEFINITIONS: RiskDefinition,
    MODEL_EVALUATIONS: ModelEvaluation,
    MODEL_DEPLOYMENTS: ModelDeployment,
    RAINFALL_EVENTS: RainfallEvent,
}


INDEX_PLAN: tuple[IndexSpec, ...] = (
    IndexSpec(USERS, (("username", pymongo.ASCENDING),), unique=True),
    IndexSpec(USERS, (("email", pymongo.ASCENDING),), sparse=True),
    IndexSpec(SALT_PANS, (("pan_id", pymongo.ASCENDING),), unique=True),
    IndexSpec(SALT_PANS, (("location_name", pymongo.ASCENDING),)),
    IndexSpec(SALT_PANS, (("updated_at", pymongo.DESCENDING),)),
    # Not unique: observations are an append-only series, and backfilled data
    # may legitimately share a timestamp. Indexed to make lookup by id cheap.
    IndexSpec(
        PAN_OBSERVATIONS,
        (("observation_id", pymongo.ASCENDING),),
    ),
    IndexSpec(
        PAN_OBSERVATIONS,
        (("pan_id", pymongo.ASCENDING), ("timestamp", pymongo.DESCENDING)),
    ),
    IndexSpec(PAN_OBSERVATIONS, (("timestamp", pymongo.DESCENDING),)),
    IndexSpec(
        PAN_OBSERVATIONS,
        (("pan_id", pymongo.ASCENDING), ("created_at", pymongo.DESCENDING)),
    ),
    # Weather is append-only for the same reason observations are, and a
    # provider can legitimately publish more than one reading for an instant, so
    # weather_id is indexed but not unique.
    IndexSpec(
        WEATHER_OBSERVATIONS,
        (("weather_id", pymongo.ASCENDING),),
    ),
    IndexSpec(
        WEATHER_OBSERVATIONS,
        (("pan_id", pymongo.ASCENDING), ("timestamp", pymongo.DESCENDING)),
    ),
    IndexSpec(WEATHER_OBSERVATIONS, (("timestamp", pymongo.DESCENDING),)),
    IndexSpec(
        WEATHER_OBSERVATIONS,
        (("pan_id", pymongo.ASCENDING), ("forecast_for", pymongo.ASCENDING)),
    ),
    IndexSpec(
        WEATHER_OBSERVATIONS,
        (("pan_id", pymongo.ASCENDING), ("created_at", pymongo.DESCENDING)),
    ),
    IndexSpec(
        DIGITAL_TWIN_STATES,
        (("pan_id", pymongo.ASCENDING), ("as_of", pymongo.DESCENDING)),
    ),
    IndexSpec(DIGITAL_TWIN_STATES, (("as_of", pymongo.DESCENDING),)),
    # Twin snapshots are immutable, so created_at only ever moves forward. It
    # gives a second ordering for the change log, which is read by record age
    # rather than by the instant the twin described.
    IndexSpec(
        DIGITAL_TWIN_STATES,
        (("pan_id", pymongo.ASCENDING), ("created_at", pymongo.DESCENDING)),
    ),
    IndexSpec(
        SIMULATION_RUNS,
        (("pan_id", pymongo.ASCENDING), ("created_at", pymongo.DESCENDING)),
    ),
    IndexSpec(
        SIMULATION_RUNS,
        (("pan_id", pymongo.ASCENDING), ("status", pymongo.ASCENDING)),
    ),
    IndexSpec(
        ML_PREDICTIONS,
        (("pan_id", pymongo.ASCENDING), ("timestamp", pymongo.DESCENDING)),
    ),
    IndexSpec(ML_PREDICTIONS, (("timestamp", pymongo.DESCENDING),)),
    IndexSpec(
        ML_PREDICTIONS,
        (("model_version", pymongo.ASCENDING),),
    ),
    IndexSpec(
        EXPLANATIONS,
        (("pan_id", pymongo.ASCENDING), ("timestamp", pymongo.DESCENDING)),
    ),
    IndexSpec(EXPLANATIONS, (("model_version", pymongo.ASCENDING),)),
    # Unique, and partial to the local kind, so one prediction can hold one
    # explanation however many times the endpoint is called. A second attribution
    # for the same row would be a second answer to a question already answered,
    # and a reader would have no way to choose between them. The partial filter
    # leaves the global summaries out of it: several of those describe one model
    # version, none is tied to a prediction, and a global row carries no
    # prediction id at all.
    IndexSpec(
        EXPLANATIONS,
        (("prediction_id", pymongo.ASCENDING),),
        unique=True,
        partial_filter={"kind": "local"},
        name="explanations_prediction_id_asc",
    ),
    # The global summary and the audit list are both read by model identity
    # rather than by version alone, since several models share a version string.
    IndexSpec(
        EXPLANATIONS,
        (("model_id", pymongo.ASCENDING), ("model_version", pymongo.ASCENDING)),
        name="explanations_model_id_asc_model_version_asc",
    ),
    IndexSpec(
        RECOMMENDATIONS,
        (("pan_id", pymongo.ASCENDING), ("issued_at", pymongo.DESCENDING)),
    ),
    IndexSpec(
        RECOMMENDATIONS,
        (("pan_id", pymongo.ASCENDING), ("priority", pymongo.ASCENDING)),
    ),
    IndexSpec(RECOMMENDATIONS, (("issued_at", pymongo.DESCENDING),)),
    # Which rule produced a recommendation, in the order they were issued under
    # it. Read to answer "what has this rule actually been recommending?", which is
    # the question a versioned rule raises and which a reader cannot answer from
    # the rule document alone.
    IndexSpec(
        RECOMMENDATIONS,
        (("rule_id", pymongo.ASCENDING), ("issued_at", pymongo.DESCENDING)),
    ),
    # The log is read by action, to see how often each of the four was issued
    # without loading every row.
    IndexSpec(
        RECOMMENDATIONS,
        (("action", pymongo.ASCENDING), ("issued_at", pymongo.DESCENDING)),
    ),
    # Unique: (name, version) is a rule version's identity, and it is what makes
    # the version allocation in the write service safe against two concurrent
    # writers choosing the same number. The service re-reads the history and
    # retries; this index is the backstop that makes the retry necessary.
    IndexSpec(
        DECISION_RULES,
        (("name", pymongo.ASCENDING), ("version", pymongo.ASCENDING)),
        unique=True,
        name="decision_rules_name_version_asc",
    ),
    # Unique: the content hash is the rule's identity, so a re-post of an unchanged
    # rule must land on the existing document rather than write a second one that
    # changes no recommendation and makes the history read as a revision.
    IndexSpec(
        DECISION_RULES,
        (("specification_hash", pymongo.ASCENDING),),
        unique=True,
        name="decision_rules_specification_hash_asc",
    ),
    IndexSpec(
        DECISION_RULES,
        (("name", pymongo.ASCENDING), ("version", pymongo.DESCENDING)),
    ),
    IndexSpec(
        OPERATOR_DECISIONS,
        (("pan_id", pymongo.ASCENDING), ("timestamp", pymongo.DESCENDING)),
    ),
    IndexSpec(
        OPERATOR_DECISIONS,
        (("recommendation_id", pymongo.ASCENDING),),
        # One decision per recommendation, stated in the database rather than only
        # in service code. The service checks before it writes, but a check and
        # an insert that are not one operation are two operators deciding the same
        # recommendation at the same moment away from each other — both see an
        # undecided recommendation and both insert. The check still runs, so the
        # refusal carries the code and the message a client expects; this is what
        # makes the guarantee hold when the two arrive together.
        #
        # Named for the constraint rather than left to the key pattern, which is
        # what the rest of this list does but not what an index carrying a
        # guarantee can do. A cluster provisioned before this became unique already
        # holds a plain index at the auto-derived name, and Mongo refuses a
        # differing spec under a name that exists (IndexKeySpecsConflict) — so
        # reusing the generated name would leave every such deployment without
        # the constraint and with a provisioning failure on every startup. A
        # distinct name is created alongside it, and the plain index is no longer
        # declared because a unique index on the same key already serves the reads
        # it was there for.
        unique=True,
        name="operator_decisions_one_decision_per_recommendation",
    ),
    IndexSpec(OPERATOR_DECISIONS, (("timestamp", pymongo.DESCENDING),)),
    IndexSpec(
        ACTUAL_OUTCOMES,
        (("pan_id", pymongo.ASCENDING), ("timestamp", pymongo.DESCENDING)),
    ),
    IndexSpec(ACTUAL_OUTCOMES, (("event_id", pymongo.ASCENDING),)),
    IndexSpec(ACTUAL_OUTCOMES, (("timestamp", pymongo.DESCENDING),)),
    # Unique: an event is measured once. Two documents for the same event would
    # count one rainfall event twice in every mean, and the service refuses the
    # second write with a 409 before this index is ever consulted. The index is
    # the backstop for a concurrent pair of writes, not the primary check.
    IndexSpec(
        ACTUAL_OUTCOMES,
        (("pan_id", pymongo.ASCENDING), ("event_id", pymongo.ASCENDING)),
        unique=True,
    ),
    IndexSpec(
        MODEL_VERSIONS,
        (("target", pymongo.ASCENDING), ("version", pymongo.ASCENDING)),
        unique=True,
    ),
    # (model_id, version) is a model version's identity, and the lookup the
    # registry and Module 10 both use to resolve "which artifact is deployed".
    # Module 08 mints a fresh model_id per run, so this is unique by
    # construction; the index is what makes the resolution cheap.
    IndexSpec(
        MODEL_VERSIONS,
        (("model_id", pymongo.ASCENDING), ("version", pymongo.ASCENDING)),
        unique=True,
        name="model_versions_model_id_version_asc",
    ),
    IndexSpec(
        MODEL_VERSIONS,
        (("status", pymongo.ASCENDING), ("created_at", pymongo.DESCENDING)),
    ),
    # The central guarantee of Module 17, stated in the database: at most one
    # model version per research target may hold `deployed`. Two deployed models
    # for one target is the exact state in which an operational prediction could
    # come from either, and which of them answered would depend on query order.
    # Partial, so every experimental/validated/retired version is free to share
    # the target. This is the backstop behind the service's refusal to displace
    # an incumbent without an explicit acknowledgement, not a replacement for it.
    IndexSpec(
        MODEL_VERSIONS,
        (("target", pymongo.ASCENDING),),
        unique=True,
        name="model_versions_one_deployed_per_target",
        partial_filter={"status": "deployed"},
    ),
    # The deployment history, in the order it happened, per model version.
    IndexSpec(
        MODEL_DEPLOYMENTS,
        (("deployment_id", pymongo.ASCENDING),),
        name="model_deployments_deployment_id_asc",
    ),
    IndexSpec(
        MODEL_DEPLOYMENTS,
        (("model_id", pymongo.ASCENDING), ("version", pymongo.ASCENDING), ("created_at", pymongo.DESCENDING)),
    ),
    # Which deployments displaced another model. Read to answer "was anything
    # ever replaced, and when", which is the question the history exists for.
    IndexSpec(
        MODEL_DEPLOYMENTS,
        (("superseded_model_id", pymongo.ASCENDING), ("created_at", pymongo.DESCENDING)),
    ),
    # Unique: the content hash is the dataset's identity, so a rebuild over
    # unchanged records must land on the existing document rather than write a
    # second one that claims the same content. The service checks first and
    # returns the existing dataset; this is the backstop for a concurrent pair.
    IndexSpec(
        RESEARCH_DATASETS,
        (("content_hash", pymongo.ASCENDING),),
        unique=True,
    ),
    # version counts distinct datasets per target in creation order, which is
    # what an experiment cites alongside the hash.
    IndexSpec(
        RESEARCH_DATASETS,
        (("target.name", pymongo.ASCENDING), ("version", pymongo.DESCENDING)),
    ),
    IndexSpec(
        RESEARCH_DATASETS,
        (("target.name", pymongo.ASCENDING), ("created_at", pymongo.DESCENDING)),
    ),
    # Unique: the content hash is the rule's identity, so re-posting an unchanged
    # definition must land on the existing document rather than write a second one
    # that changes no label and would make the history read as a revision. The
    # service checks first and returns the existing definition; this is the
    # backstop for a concurrent pair of writes.
    IndexSpec(
        RISK_DEFINITIONS,
        (("specification_hash", pymongo.ASCENDING),),
        unique=True,
    ),
    # version counts distinct revisions of one rule name in creation order, which
    # is what an experiment cites alongside the hash. Unique because the service
    # allocates the next version by reading the newest and adding one, which two
    # concurrent writers can both do: without uniqueness both inserts would
    # succeed and the history would hold two documents claiming to be version 3,
    # so "which version was this" would have two answers. A unique index makes the
    # collision a failure the writer can retry, which is the only honest outcome.
    IndexSpec(
        RISK_DEFINITIONS,
        (("name", pymongo.ASCENDING), ("version", pymongo.DESCENDING)),
        unique=True,
    ),
    IndexSpec(
        RISK_DEFINITIONS,
        (("created_at", pymongo.DESCENDING),),
    ),
    # Unique: one evaluation per model version, because the run is deterministic
    # given the recorded seed. The service reads the existing document and
    # replaces it rather than inserting, so a second run is a refresh and not a
    # second claim to have measured the same held-out rows; the unique index is
    # the backstop for a concurrent pair of evaluations, which is the only way two
    # records for one version could otherwise both exist.
    IndexSpec(
        MODEL_EVALUATIONS,
        (("model_id", pymongo.ASCENDING), ("version", pymongo.ASCENDING)),
        unique=True,
    ),
    # evaluation_id is derived from (model_id, version) and so is unique by
    # construction, but it is the field a URL and an export name, so it is indexed
    # to make the lookup cheap rather than to enforce something new.
    IndexSpec(
        MODEL_EVALUATIONS,
        (("evaluation_id", pymongo.ASCENDING),),
        unique=True,
    ),
    # Listing a target's evaluations newest first is the comparison view, and
    # target is the field that narrows it.
    IndexSpec(
        MODEL_EVALUATIONS,
        (("target", pymongo.ASCENDING), ("evaluated_at", pymongo.DESCENDING)),
    ),
    # Unique: the content hash is an experiment's identity, so re-posting the
    # identical record must land on the existing document rather than write a
    # second one that claims to be an independent reproduction. The service
    # checks first and returns the existing experiment; this is the backstop for
    # a concurrent pair of identical writes.
    IndexSpec(
        RESEARCH_EXPERIMENTS,
        (("content_hash", pymongo.ASCENDING),),
        unique=True,
    ),
    # The ledger is consumed newest first, and filtered by the facts a reader
    # scans for.
    IndexSpec(
        RESEARCH_EXPERIMENTS,
        (("timestamp", pymongo.DESCENDING),),
    ),
    IndexSpec(
        RESEARCH_EXPERIMENTS,
        (("model", pymongo.ASCENDING), ("timestamp", pymongo.DESCENDING)),
    ),
    IndexSpec(
        RESEARCH_EXPERIMENTS,
        (("dataset_hash", pymongo.ASCENDING), ("timestamp", pymongo.DESCENDING)),
    ),
    IndexSpec(
        RESEARCH_EXPERIMENTS,
        (("target", pymongo.ASCENDING), ("timestamp", pymongo.DESCENDING)),
    ),
    # Module 20 - field-validation events are listed per pan newest first, and
    # each event's identity is its event_id. Reports are quoted by event_id, so
    # it is indexed to make the lookup cheap; it is not unique because the module
    # deliberately allows a correction to be filed as a new event rather than
    # edited in place, exactly like observations.
    IndexSpec(
        RAINFALL_EVENTS,
        (("event_id", pymongo.ASCENDING),),
        name="rainfall_events_event_id_asc",
    ),
    IndexSpec(
        RAINFALL_EVENTS,
        (("pan_id", pymongo.ASCENDING), ("event_timestamp", pymongo.DESCENDING)),
    ),
)


def registry_collections() -> tuple[str, ...]:
    return tuple(COLLECTION_MODELS)


def resolves_to_field(model: Any, path: str) -> bool:
    """Whether ``path`` names a field on ``model``, following dotted sub-documents.

    Mongo indexes a sub-document field as ``parent.child``, so an index on
    ``target.name`` is legitimate against a model whose ``target`` is a nested
    model rather than a plain mapping. Walking the path is what keeps such a spec
    from being reported as an unknown field, which would refuse the whole plan
    and leave the collection with no indexes at all.
    """
    current: Any = model
    for part in path.split("."):
        fields = getattr(current, "model_fields", None)
        if not isinstance(fields, dict) or part not in fields:
            return False
        current = fields[part].annotation
    return True


def validate_index_plan() -> None:
    errors: list[str] = []
    for spec in INDEX_PLAN:
        model = COLLECTION_MODELS.get(spec.collection)
        if model is None:
            errors.append(f"{spec.collection}: no model is registered")
            continue
        for field, _ in spec.keys:
            if not resolves_to_field(model, field):
                errors.append(f"{spec.collection}.{spec.name}: unknown field {field}")
    if errors:
        raise ValueError("invalid MongoDB index plan: " + "; ".join(errors))


async def ensure_indexes(database: Any, *, strict: bool = False) -> IndexReport:
    """Provision the index plan on ``database``.

    Accepts an already-created database or a zero-argument provider returning
    one, exactly as :class:`app.db.repository.MongoRepository` does, so index
    provisioning can never be the one caller that treats a provider as a
    database. Provisioning is idempotent: ``create_index`` names every index,
    so a second call against an already-provisioned cluster is a no-op that
    returns the same report rather than a second set of indexes.
    """
    database = resolve_database(database)
    validate_index_plan()
    created: list[str] = []
    failures: list[IndexFailure] = []
    for spec in INDEX_PLAN:
        kwargs: dict[str, Any] = {"unique": spec.unique, "name": spec.name}
        if spec.sparse:
            kwargs["sparse"] = True
        if spec.partial_filter:
            kwargs["partialFilterExpression"] = spec.partial_filter
        try:
            await database[spec.collection].create_index(list(spec.keys), **kwargs)
        except PyMongoError as exc:
            failure = IndexFailure(spec.collection, spec.name, str(exc))
            failures.append(failure)
            logger.warning("Could not create index %s: %s", spec.name, exc)
        else:
            created.append(spec.name)
    report = IndexReport(tuple(created), tuple(failures))
    if strict and not report.ready:
        names = ", ".join(f"{failure.collection}/{failure.name}" for failure in report.failures)
        raise IndexProvisioningError(f"MongoDB indexes could not be provisioned: {names}")
    return report
