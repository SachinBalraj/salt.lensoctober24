"""Module 17 - the HTTP contract for controlled model deployment.

The contract exists to make one thing hard to do by accident: put a model into
operational service without a person having decided to.

**Every lifecycle change carries a reason and a named actor, and neither is
optional.** ``reason`` is a required non-empty string on all three transitions.
A deployment whose reason is optional is a deployment an operator can perform by
clicking through a dialog, which is how models get into service by accident. The
stored reason is the researcher's own words, not a status code restated back at
them.

**The version acted on is named in the body, never inferred from "the latest".**
A deployment that resolves a version for you can deploy the wrong one after a
retrain. The path names the model; the body names the version; the response echoes
both plus the evaluation the decision rests on, so a client that sent the request
cannot disagree with what happened.

**A replacement must be acknowledged before it happens.** Deploying over an
incumbent for the same research target takes a ``supersede`` confirmation and a
``supersede_reason``. Without them the request is refused with 409 and the
incumbent named. This is the requirement "never silently replace the deployed
model after retraining" enforced at the request boundary rather than relied on as
a matter of convention: a retraining run cannot reach the deployed model, because
a retraining run writes a new version in ``experimental`` and nothing else.

**Validation and deployment both require an evaluation to exist.** A model with no
``evaluation_id`` cannot be validated or deployed, and the response says which
report was cited. Deploying on an unevaluated model is not a controlled decision,
it is an untested one.

The response models are the stored record. ``features``, ``hyperparameters``,
``evaluation_metrics``, ``target_definition`` and ``split_strategy`` are returned
as they are held, and the evaluation metrics are Module 09's readings -- objects
carrying ``computed`` and ``reason`` -- rather than bare floats, so this module
cannot reintroduce the one thing it is downstream of.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ModelDeploymentAction, ModelStatus, ResearchTarget

#: The only statuses a model version may be in. Restated here so the response
#: contract and the stored vocabulary cannot drift apart unnoticed: a status this
#: module does not list is one it did not write.
LIFECYCLE_STATUSES: tuple[ModelStatus, ...] = (
    ModelStatus.EXPERIMENTAL,
    ModelStatus.VALIDATED,
    ModelStatus.DEPLOYED,
    ModelStatus.RETIRED,
)


class LifecycleRequestBase(BaseModel):
    """Fields every transition needs, whichever direction it moves in."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^v?\d+\.\d+\.\d+$",
        description=(
            "The exact model version to act on. Required rather than defaulted to "
            "the newest, because the newest version is the one a retraining run "
            "just wrote and defaulting to it is how a deployment ends up aimed at "
            "a model nobody looked at."
        ),
    )
    reason: str = Field(
        min_length=1,
        max_length=1000,
        description=(
            "Why, in the researcher's own words. Stored on the model version and "
            "in the append-only deployment history."
        ),
    )
    actor: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "Who is making the change. SaltLens does not authenticate this path "
            "yet, so it is a recorded claim; it is stored so that gap is visible in "
            "the history rather than absent from it."
        ),
    )


class DeployRequestBody(LifecycleRequestBase):
    """Request a model version for operational service."""

    supersede: bool = Field(
        default=False,
        description=(
            "Acknowledge that this deployment displaces the model currently "
            "deployed for the same research target. Without it, deploying over an "
            "incumbent is refused."
        ),
    )
    supersede_reason: str | None = Field(default=None, max_length=1000)


class ValidateRequestBody(LifecycleRequestBase):
    """Accept a model's evaluation report, making it eligible for deployment."""


class RetireRequestBody(LifecycleRequestBase):
    """Withdraw a model version from service."""


class DeploymentHistoryEntry(BaseModel):
    """One step of a model version's lifecycle, as stored."""

    model_config = ConfigDict(from_attributes=True)

    deployment_id: str
    action: ModelDeploymentAction
    from_status: ModelStatus
    to_status: ModelStatus
    reason: str
    actor: str
    evaluation_id: str | None
    superseded_model_id: str | None
    superseded_version: str | None
    selection_criterion: str | None
    created_at: datetime


class EvaluationSummary(BaseModel):
    """The stored evaluation metrics, as Module 09's readings.

    Metrics are the reading objects rather than floats, unmodified. This module
    does not re-shape, round or re-label them, so a figure shown in the registry
    is the same figure Module 09 computed and the same figure its own export
    writes. ``absent_metrics`` names the ones that were not calculated, which is
    what stops a reader from reading a missing value as a zero.
    """

    evaluation_id: str
    computed: bool
    evaluated_at: datetime
    dataset_id: str | None
    dataset_version: int | None
    evaluated_rows: int = Field(
        description=(
            "How many held-out rows the metrics were computed on. The denominator "
            "is stated next to the scores because a figure without one cannot be "
            "compared to anything, including another figure from the same report."
        )
    )
    metrics: dict[str, Any]
    absent_metrics: list[str]
    has_cross_validation: bool
    per_class_count: int
    #: Module 09's answer to "which rule chose the selected row", copied here so
    #: a deployment decision can cite the criterion that was in force. Null when
    #: the report names none, which is reported rather than invented.
    selection_criterion: str | None


class ModelVersionRecord(BaseModel):
    """One model version, as stored.

    Every field the deployment requirement names, under the name the requirement
    gives it: ``model_id`` is the model's name in the registry, ``version`` its
    version, ``dataset_version`` and ``dataset_content_hash`` identify the dataset
    revision, ``trained_at`` is the training date, ``feature_names`` the features,
    ``parameters`` the hyperparameters, ``metrics`` the metrics the training run
    itself recorded, ``target`` the target definition, ``split_strategy`` how the
    held-out set was chosen, and ``status`` where the version stands.
    """

    model_id: str
    version: str
    status: ModelStatus
    target: ResearchTarget
    algorithm: str | None
    framework: str | None
    dataset_id: str | None
    dataset_version: int | None
    dataset_content_hash: str | None
    trained_at: datetime | None
    feature_names: list[str]
    hyperparameters: dict[str, Any]
    training_metrics: dict[str, Any]
    target_definition: dict[str, Any]
    split_strategy: str | None
    split_detail: dict[str, Any]
    random_seed: int | None
    library_versions: dict[str, str]
    artifact_path: str | None
    training_duration_seconds: float | None

    validated_at: datetime | None
    validated_by: str | None
    deployed_at: datetime | None
    deployed_by: str | None
    retired_at: datetime | None
    retired_by: str | None
    lifecycle_reason: str | None
    superseded_by_model_id: str | None
    superseded_by_version: str | None

    evaluation: EvaluationSummary | None = Field(
        default=None,
        description=(
            "The stored evaluation report, or null when the model has never been "
            "evaluated. A null here is why the model cannot be validated or "
            "deployed, and it is shown rather than left as an empty metrics "
            "object that would read as a model that scored nothing."
        ),
    )
    #: What this version can be moved to next, given its current status. Computed
    #: server-side from the lifecycle rules so a client cannot offer a transition
    #: that will be refused, and so the reason a transition is unavailable is on
    #: the same object as the transition.
    available_transitions: list[str]
    blocked_reason: str | None = Field(
        default=None,
        description=(
            "Why this version cannot move anywhere, when it cannot: no evaluation "
            "report, already deployed, or retired. Null when a transition is "
            "available."
        ),
    )


class RegistryListResponse(BaseModel):
    """The inspection view: every model version, newest first, with its state."""

    models: list[ModelVersionRecord]
    total: int = Field(
        description="How many model versions the registry holds, across all filters."
    )
    deployed: list[ModelVersionRecord] = Field(
        description=(
            "The versions currently permitted to answer operational requests, one "
            "per research target. Present on every response so the deployed set is "
            "never something a client has to assemble by filtering."
        )
    )
    statuses: dict[str, int] = Field(
        description="How many versions hold each lifecycle status."
    )


class DeploymentResultResponse(BaseModel):
    """What one lifecycle request actually did."""

    status: Literal["ok"] = "ok"
    action: ModelDeploymentAction
    from_status: ModelStatus
    to_status: ModelStatus
    #: The full post-transition record, so the client learns the new state from
    #: the server rather than predicting it.
    model: ModelVersionRecord
    history_entry: DeploymentHistoryEntry = Field(
        description=(
            "The append-only document written for this change. Returned so the "
            "event is visible at the moment it is caused, and so a caller can cite "
            "its id when disputing the change."
        )
    )
    superseded: ModelVersionRecord | None = Field(
        default=None,
        description=(
            "The model version this deployment displaced, after it was retired. "
            "Null unless a replacement actually happened, which is the only case "
            "where a researcher needs to be told what they replaced."
        )
    )


class RegistryOverviewResponse(BaseModel):
    """What this module is, and what it currently permits."""

    module: str
    module_number: int
    description: str
    endpoints: list[str]
    statuses: list[str]
    #: One entry per research target that has a deployed model, naming it. The
    #: answer to "what is answering operational requests right now", which is the
    #: question this whole module exists to make answerable.
    deployed: list[dict[str, Any]]
    counts: dict[str, int]
    #: The forward edges of the lifecycle, as stored. Published so a client can
    #: offer only the transitions that exist rather than discovering the rules by
    #: being refused.
    transitions: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Status to the statuses reachable from it. Retired maps to an empty "
            "list, which is how the terminal state is communicated."
        ),
    )
    #: The metric keys shown beside each model in the inspection view. They are "
    #: the comparison table's own columns, published so a client renders the "
    #: registry's numbers under the same names Module 09 uses.
    summary_metrics: list[str] = Field(default_factory=list)
    guarantees: list[str] = Field(
        default_factory=list,
        description=(
            "What this module guarantees about how a model reaches service. "
            "Stated in the payload so a client reading only the status response "
            "learns that a retrain cannot replace the deployed model."
        ),
    )


__all__ = [
    "LIFECYCLE_STATUSES",
    "DeployRequestBody",
    "DeploymentHistoryEntry",
    "DeploymentResultResponse",
    "EvaluationSummary",
    "ModelVersionRecord",
    "RegistryListResponse",
    "RegistryOverviewResponse",
    "RetireRequestBody",
    "ValidateRequestBody",
]
