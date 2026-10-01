"""Module 8 - the HTTP contract for training experiments.

Three decisions shape this contract, and all three are about what a client is
allowed to believe about a response.

**Refusal is a status code, not a 200 with a message in it.** When the data
cannot support a model, ``POST /api/ml/train`` returns 422 with the exact
sentence ``"Insufficient labelled data for reliable model training."`` in
``detail``, plus a ``reasons`` array naming the counts that failed. A 200 carrying
a refusal would be indistinguishable from a 200 carrying a model, and a client
that checked only the status code would report a training run that did not happen.

The reasons travel in a header-adjacent field rather than being concatenated into
``detail``, because ``detail`` is the fixed string the contract promises and
assembling reasons into it would change its shape on every dataset.

**The response describes what was actually done.** It carries every candidate's
metrics, not only the selected model's, and the split that produced them. A
response showing one score cannot be checked against the alternatives, and the
comparison between a simple model and an ensemble is the number that tells a
researcher whether the data is big enough to support one.

**Random Forest is never silently chosen.** It is fitted and scored on every run
and its score is returned, but the selected algorithm is not it unless the request
asked for it by name. When it did, the request's ``allow_random_forest`` is echoed
in the response and stored on the model version, so a reader can tell a deliberate
promotion from a default.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.ml.pipeline import DEFAULT_RANDOM_SEED, DEFAULT_TEST_SIZE
from app.ml.splitting import DEFAULT_SPLIT_STRATEGY, SPLIT_STRATEGIES
from app.ml.sufficiency import INSUFFICIENT_DATA_MESSAGE
from app.models.enums import ResearchTarget

#: The literal reason a refusal carries, exposed so a client and a test can both
#: reference it rather than each holding their own copy of the sentence.
INSUFFICIENT_DATA_REASON = INSUFFICIENT_DATA_MESSAGE


class TrainRequestBody(BaseModel):
    """What to train. Every field has a defensible default.

    ``extra="forbid"`` so a misspelled field is a 422 rather than a silently
    ignored setting. A request that says ``split_startegy`` and means a different
    split would otherwise train happily under a default the caller did not choose.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description=(
            "An optional human label for this model, shown in the registry and on "
            "a prediction. It names the model for a reader; it does not identify "
            "it, and nothing resolves a model by it. Omitted, the model is shown by "
            "the id the registry assigned."
        ),
    )
    dataset_id: str = Field(
        min_length=1,
        max_length=64,
        description="The stored dataset to fit. Its manifest defines the features and target.",
    )
    split_strategy: str = Field(
        default=DEFAULT_SPLIT_STRATEGY,
        description=(
            "How the held-out rows are chosen. 'chronological' trains on the "
            "earliest rows and tests on the most recent, which is the default "
            "because it is the only one that does not let a later observation "
            "inform an earlier prediction. 'grouped_by_pan' holds out whole pans, "
            "for a question about generalising to an unseen pan. "
            "'per_pan_chronological' cuts time within each pan and does NOT "
            "evaluate an unseen pan. 'random' is stratified and seeded, and is the "
            "weakest choice on time-ordered data."
        ),
    )
    test_size: float = Field(
        default=DEFAULT_TEST_SIZE,
        gt=0,
        lt=1,
        allow_inf_nan=False,
        description="Fraction of rows held out for evaluation.",
    )
    random_seed: int = Field(
        default=DEFAULT_RANDOM_SEED,
        ge=0,
        description=(
            "Seed for every stochastic step. Recorded on the model version, so a "
            "run can be reproduced exactly."
        ),
    )
    allow_random_forest: bool = Field(
        default=False,
        description=(
            "Permit Random Forest to be selected. It is fitted and scored on every "
            "run regardless; this flag only allows it to win. Recorded on the "
            "model version when set."
        ),
    )


class InsufficientDataResponse(BaseModel):
    """The 422 body. One fixed sentence, plus the counts that failed."""

    model_config = ConfigDict(extra="forbid")

    detail: str = Field(
        default=INSUFFICIENT_DATA_MESSAGE,
        description="The fixed refusal sentence. Matched by clients and tests.",
    )
    reasons: list[str] = Field(
        default_factory=list,
        description=(
            "What specifically failed, e.g. a class with too few rows. Empty "
            "unless the refusal came from the API rather than the pipeline."
        ),
    )
    dataset_id: str | None = None
    labelled_rows: int | None = None
    class_counts: dict[str, int] = Field(default_factory=dict)
    distinct_pans: int | None = None
    # Stated so a client can tell the refusals apart without parsing prose:
    # insufficient data is fixable by gathering data, a drifted one by rebuilding,
    # and an invalid one by changing the request.
    refusal: str | None = Field(
        default=None,
        description=(
            "One of 'insufficient_data', 'dataset_drift', 'unsupported_target' or "
            "'invalid_request'."
        ),
    )


class CandidateMetrics(BaseModel):
    """One candidate's held-out scores. Absent metrics are omitted, not zeroed."""

    model_config = ConfigDict(extra="allow")

    accuracy: float | None = None
    balanced_accuracy: float | None = None
    f1_macro: float | None = None
    precision_macro: float | None = None
    recall_macro: float | None = None
    matthews_corrcoef: float | None = None
    roc_auc: float | None = None
    brier_score: float | None = None
    log_loss: float | None = None


class TrainResponse(BaseModel):
    """A completed run: what was fitted, to what, and how it scored.

    Carries the full comparison rather than just the winner, because the point of
    fitting four candidates is the comparison. It also carries the seed and the
    library versions, so the run is reproducible from the response alone.
    """

    model_config = ConfigDict(extra="forbid")

    model_id: str
    version: str
    target: ResearchTarget
    algorithm: str = Field(description="The selected candidate. Never random_forest unless requested.")
    status: str = Field(description="'candidate'. A held-out score is not a deployment decision.")
    metrics: dict[str, float] = Field(description="The selected model's held-out scores.")
    candidate_metrics: dict[str, dict[str, float]] = Field(
        description="Every candidate's scores, including the withheld Random Forest."
    )
    selection_reason: str
    allow_random_forest: bool = Field(
        description="Whether Random Forest was permitted to be selected for this run."
    )
    random_seed: int
    split_strategy: str
    split_detail: dict[str, Any] = Field(
        description="How the held-out rows were chosen, including per-pan and time boundaries."
    )
    feature_names: list[str]
    preprocessing: dict[str, Any] = Field(
        description="What the fitted pipeline learned: imputation, categories, scaling."
    )
    dataset_id: str
    dataset_version: int
    dataset_content_hash: str
    library_versions: dict[str, str]
    trained_at: datetime
    training_duration_seconds: float
    artifact_path: str


class ExperimentSummary(BaseModel):
    """A registered model version, for the list endpoint."""

    model_config = ConfigDict(extra="forbid")

    model_id: str
    version: str
    target: ResearchTarget
    status: str
    algorithm: str | None = None
    dataset_id: str | None = None
    dataset_version: int | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    feature_names: list[str] = Field(default_factory=list)
    split_strategy: str | None = None
    random_seed: int | None = None
    library_versions: dict[str, str] = Field(default_factory=dict)
    trained_at: datetime | None = None
    training_duration_seconds: float | None = None
    artifact_path: str | None = None


class ExperimentListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiments: list[ExperimentSummary]
    count: int
    available_targets: list[str] = Field(default_factory=list)
    split_strategies: list[str] = Field(default_factory=lambda: list(SPLIT_STRATEGIES))


__all__ = [
    "INSUFFICIENT_DATA_REASON",
    "CandidateMetrics",
    "ExperimentListResponse",
    "ExperimentSummary",
    "InsufficientDataResponse",
    "TrainRequestBody",
    "TrainResponse",
]
