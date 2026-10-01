"""Module 10 - the HTTP contract for operational risk definitions.

Four things are worth stating about this contract, because each of them is a
decision rather than a convention.

**A definition is written with a draft, not a document.** A stored definition
carries an identifier, a version number, a content hash and a supersedes link,
none of which a client can honestly supply: the hash depends on the rest of the
document, the version depends on stored history, and the identifier is the
database's business. So the request body validates against
:class:`~app.models.risk_definition.RiskDefinitionDraft` and the service assigns
identity. A client that had to compute the hash would be reimplementing the
service's canonical encoding, and one that guessed it would store a rule whose
cited identity does not describe it.

**Writing is 201, re-writing is 200.** An unchanged rule posted twice returns the
first document with ``created: false`` rather than a second version. That is not
a lesser success: it is the only honest answer, because a version that changes no
label is a revision nobody made, and a history containing one misrepresents how
often the rule was actually reconsidered.

**There is no update and no delete endpoint.** Both are absent on purpose. A rule
that can be edited in place leaves every model fitted to it undescribable, because
the thresholds behind version 2 are gone. Changing a rule means posting a new
version, and the history endpoint shows what changed.

**The distribution endpoint exists, and the prediction endpoint does not.** A
researcher can ask what a rule's labels look like over the stored records, with
the imbalance warnings, before anything is fitted. There is no way to ask this
module for a risk *prediction*, because no model is trained and a module that
answered would be inventing a number. Its status endpoint says so.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.base import UtcDateTime
from app.models.enums import RiskCombinator, RiskLabelBasis
from app.models.risk_definition import (
    RiskClassDistribution,
    RiskCriterion,
    RiskLabel,
)


class RiskDefinitionCreateResponse(BaseModel):
    """What a write to the definitions collection did.

    ``created`` is the field that matters. A caller re-posting an unchanged rule
    has done nothing, and reporting that as a fresh version would put an entry in
    the rule's history that no revision produced.
    """

    model_config = ConfigDict(extra="forbid")

    created: bool = Field(
        description=(
            "True when a new version was written. False when the identical rule "
            "already existed, in which case the stored document is returned "
            "unchanged and the version number did not advance."
        )
    )
    reason: str | None = Field(
        default=None,
        description="Why nothing was written, when nothing was.",
        examples=["identical_definition_already_stored"],
    )
    definition: RiskDefinitionRead


class RiskDefinitionRead(BaseModel):
    """A stored definition, as the API returns it.

    A separate model from the document itself so the HTTP shape can stay stable
    while the stored representation carries fields an API reader has no use for.
    """

    model_config = ConfigDict(extra="forbid")

    definition_id: str
    name: str
    version: int
    label_column: str
    classes: list[RiskLabel]
    criteria: list[RiskCriterion]
    combinator: RiskCombinator
    unmatched_label: str | None
    label_basis: RiskLabelBasis
    variables_used: list[str]
    operational_rationale: str
    authored_by: str | None
    specification_hash: str
    short_hash: str = Field(
        description=(
            "The leading characters of the specification hash, for logs and "
            "filenames. Never an identity: a truncated digest can collide."
        )
    )
    supersedes_definition_id: str | None
    created_at: UtcDateTime
    class_names: tuple[str, ...] = Field(
        description="The vocabulary, least severe first, as the researcher ordered it."
    )

    @classmethod
    def from_definition(cls, definition: object) -> RiskDefinitionRead:
        return cls(
            definition_id=definition.definition_id,
            name=definition.name,
            version=definition.version,
            label_column=definition.label_column,
            classes=list(definition.classes),
            criteria=list(definition.criteria),
            combinator=definition.combinator,
            unmatched_label=definition.unmatched_label,
            label_basis=definition.label_basis,
            variables_used=list(definition.variables_used),
            operational_rationale=definition.operational_rationale,
            authored_by=definition.authored_by,
            specification_hash=definition.specification_hash,
            short_hash=definition.short_hash,
            supersedes_definition_id=definition.supersedes_definition_id,
            created_at=definition.created_at,
            class_names=definition.class_names,
        )


class RiskDefinitionListResponse(BaseModel):
    """A page of definitions, newest first."""

    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=0)
    definitions: list[RiskDefinitionRead]


class RiskVersionResponse(BaseModel):
    """One rule's whole history, oldest first, so it reads forwards."""

    model_config = ConfigDict(extra="forbid")

    name: str
    count: int = Field(ge=0)
    versions: list[RiskDefinitionRead]


class RiskVariableResponse(BaseModel):
    """One measurable quantity a rule may reference."""

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str
    unit: str | None
    reads_from: str
    leakage_class: str
    known_at_anchor: bool = Field(
        description=(
            "Whether a value of this variable was available at the decision "
            "moment. False means any rule reading it produces a retrospective "
            "label."
        )
    )
    source_collection: str
    source_field: str
    operational_meaning: str
    derivation: str
    nullable_reason: str | None
    operands: list[str] = Field(
        default_factory=list,
        description="The catalogued figures a derived variable is the difference of.",
    )


class RiskCatalogResponse(BaseModel):
    """Everything a researcher may reference, and the policy applied to the result.

    Served from the catalogue the evaluator actually reads, so it cannot drift
    from the rules that will be enforced.
    """

    model_config = ConfigDict(extra="forbid")

    variables: list[RiskVariableResponse]
    retrospective_variables: list[str] = Field(
        description=(
            "The variables measured after the anchor. A rule reading any of them "
            "is labelled retrospective and cannot be used to forecast the event it "
            "is graded against."
        )
    )
    severe_imbalance_ratio: float = Field(
        description=(
            "Largest tolerated ratio between the most and least populated classes. "
            "A threshold and not a refusal: a distribution past it can still be "
            "worth fitting after rebalancing, and the response is a decision."
        )
    )
    rare_class_minimum_rows: int = Field(
        description=(
            "Fewest rows the rarest populated class must hold for a per-class figure "
            "to be an estimate rather than a single observation."
        )
    )


class RiskDistributionRequest(BaseModel):
    """Which stored records to derive a rule's labels over, before training.

    A distribution is only meaningful against some bounded set of records, so the
    window and pan filter are part of the question rather than an afterthought.
    Nothing is stored: the rows are derived now, the report describes them, and no
    model is fitted to them by asking.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    horizon_hours: float = Field(
        default=24.0,
        gt=0.0,
        le=24 * 30,
        allow_inf_nan=False,
        description=(
            "The horizon the labels are derived over. It matters for a "
            "retrospective rule, whose recorded outcome is read at or after "
            "anchor plus horizon, and for the rain-event filter below."
        ),
    )
    pan_ids: list[str] | None = Field(
        default=None,
        max_length=200,
        description="Restrict to these pans. Omit for every pan with a recorded anchor.",
    )
    start: datetime | None = Field(
        default=None, description="Earliest anchor instant to consider."
    )
    end: datetime | None = Field(
        default=None, description="Latest anchor instant to consider."
    )
    lookback_hours: float = Field(
        default=72.0,
        ge=0.0,
        le=24 * 30,
        allow_inf_nan=False,
        description="How far back the rainfall-window variables reach from each anchor.",
    )
    max_source_records: int = Field(
        default=20_000,
        ge=1,
        le=200_000,
        description=(
            "Cap on records read per collection. Reaching it makes the report a "
            "bounded sample rather than the whole store, and says so."
        ),
    )
    require_rain_event: bool = Field(
        default=False,
        description=(
            "Keep only rows whose horizon window contains recorded rainfall. A real "
            "restriction on which rows a rule gets to see, so it is stated rather "
            "than applied silently."
        ),
    )

    @property
    def pan_id_tuple(self) -> tuple[str, ...]:
        return tuple(self.pan_ids or ())


class RiskDistributionResponse(BaseModel):
    """A rule's labels over the stored records, and what to decide about them.

    The document a researcher is asked to read *before* a model is fitted. Class
    balance is a joint property of a rule and a set of pans, visible in neither
    alone, and it is the last place a 97/2/1 split can be noticed before an
    accuracy figure is quoted as though it meant something.
    """

    model_config = ConfigDict(extra="forbid")

    definition: RiskDefinitionRead
    distribution: RiskClassDistribution
    record_count: int = Field(ge=0, description="Rows evaluated, labelled or not.")
    is_usable_for_training: bool = Field(
        description=(
            "Whether this distribution supports fitting and evaluating a model. "
            "False when nothing was labelled, when everything landed in one class, "
            "or when the split is too lopsided or too thinly populated for a "
            "per-class figure to mean anything."
        )
    )
    truncated_collections: list[str] = Field(
        default_factory=list,
        description="Collections whose read cap was reached, if any.",
    )
    trained_model: None = Field(
        default=None,
        description=(
            "Always null. This module derives labels and reports their "
            "distribution; it fits no model, and the field exists so a reader of "
            "this response does not have to wonder whether asking triggered "
            "training."
        ),
    )


class RiskPredictionRequest(BaseModel):
    """The one operational question: this pan, now.

    Deliberately thin. The request names *what* is being asked about and nothing
    about how the answer should be computed — no feature values, no model id, no
    window. The features are read from the pan's current digital twin state, and
    the model is the deployed one, so a client cannot obtain a prediction from
    inputs it supplied itself, and cannot ask a model other than the deployed one.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    pan_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="The pan to predict for.",
    )
    as_of: datetime | None = Field(
        default=None,
        description=(
            "Optional. Omitted, the prediction is made from the pan's current twin "
            "state, which is the ordinary operational question. Given, the newest "
            "twin state recorded at or before this instant is used, so a prediction "
            "anchored at a past moment is made from what the system believed then. "
            "There is no instant for which no state exists: the request is refused "
            "rather than answered from today's view of the past."
        ),
    )


class RiskPredictionResponse(BaseModel):
    """One risk class, its probability, and everything needed to trace it.

    The provenance fields are not decoration. ``model_id`` and ``model_version`` say
    which artifact answered, ``input_snapshot_id`` says which state it was given,
    ``deployment_id`` says which recorded decision put it in service, and
    ``risk_definition_id``/``version`` say whose vocabulary the class comes from. A
    risk figure a reader cannot trace to those is a figure they have to take on
    trust.
    """

    prediction_id: str
    pan_id: str
    #: When the model was run. Distinct from ``as_of``, which is the instant of the
    #: twin state it was given: a prediction is normally made long after the state
    #: it describes, and a reader checking the two needs them apart.
    predicted_at: datetime
    #: The instant of the twin state the features were read from.
    as_of: datetime
    #: The predicted class, as a name declared by the risk rule. Not a number: an
    #: operational risk prediction is a statement about a rule's class, and the
    #: class name is the only unambiguous form of it.
    risk_class: str
    #: The model's probability for that class, exactly as ``predict_proba`` produced
    #: it. Null when the deployed model's estimator reports no per-class
    #: probabilities: the class is still the model's own, and no number is supplied
    #: in its place, because a figure this module made up would be read as the
    #: model's.
    probability: float | None = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)
    #: Every class the model could have emitted, with its probability. Includes
    #: classes the rule declares and classes it does not, because a model that
    #: predicted a class outside the rule's vocabulary is a fact worth seeing — the
    #: service refuses in that case, so the two lists are usually the same. Empty
    #: when the model reports no probabilities.
    class_probabilities: dict[str, float]
    defined_classes: list[str] = Field(
        description="The classes the risk rule declares, in the model's own order."
    )
    model_id: str
    model_name: str | None = Field(
        default=None,
        description=(
            "The researcher's label for the model, when one was given at training "
            "time. Null otherwise; the model is then identified by ``model_id`` "
            "alone, which is always present."
        ),
    )
    model_version: str
    algorithm: str | None
    deployment_id: str | None
    deployed_at: datetime | None
    #: The content hash of the twin state and feature row the prediction was made
    #: from. Recomputable from the stored prediction, so a stored number can be
    #: checked against the input it claims.
    input_snapshot_id: str
    #: The twin state the features came from.
    twin_id: str
    twin_as_of: datetime
    risk_definition_id: str
    risk_definition_version: int
    dataset_id: str | None = None
    dataset_version: int | None = None
    selection_criterion: str | None = Field(
        default=None,
        description=(
            "The rule that selected this model, quoted from Module 8. Carried here "
            "so a reader of a single prediction can see that the model was chosen by "
            "a documented criterion rather than by being the only one available."
        ),
    )
    features_used: list[str] = Field(
        default_factory=list,
        description="The model's declared features that carried a value from the twin.",
    )
    features_absent: list[str] = Field(
        default_factory=list,
        description=(
            "The declared features the twin could not supply. These were imputed by "
            "the preprocessor the model was fitted with, and are listed rather than "
            "filled so a reader can see how much of the row was real."
        ),
    )
    features_imputed: list[str] = Field(
        default_factory=list,
        description="The same names as ``features_absent``, named for what happens to them.",
    )
    used_physics_prediction: bool = Field(
        default=False,
        description=(
            "Whether Module 06's mass balance ran for this row, from the twin's "
            "brine state and the forecast it carries."
        ),
    )
    input_source: str = Field(
        default="current_digital_twin_state",
        description="Where the features came from. One value: the current twin state.",
    )
    explanation_status: Literal["available", "unavailable"] = Field(
        default="unavailable",
        description=(
            "Whether Module 11 could attribute this prediction to its input "
            "features: \"available\" or \"unavailable\". Reported beside the "
            "answer rather than inside it, because an explanation is additional to "
            "the model's output and must never be read as part of it. An "
            "\"unavailable\" status is not a failed prediction: the class and "
            "probability above are the model's own, complete and unaffected."
        ),
    )
    explanation_id: str | None = Field(
        default=None,
        description=(
            "The stored attribution for this prediction, when one was computed. "
            "Read it with ``GET /api/explain/predictions/{prediction_id}``, which "
            "returns the per-feature contributions, the base value they are measured "
            "against, and the method that produced them."
        ),
    )
    explanation_refusal: str | None = Field(
        default=None,
        description=(
            "The machine-readable reason explainability is unavailable. Null when "
            "it is available, or when it has simply not been asked for. Named "
            "rather than left blank so a client can branch on it without reading "
            "prose."
        ),
    )
    explanation_refusal_detail: str | None = Field(
        default=None,
        description="The sentence explaining the refusal, in the same words everywhere else.",
    )
    row: dict[str, Any] | None = Field(
        default=None,
        deprecated=True,
        description=(
            "Superseded by ``input_snapshot_id``, ``twin_id``, ``features_used`` and "
            "``features_absent``, which carry the same information under names that "
            "say what they are."
        ),
    )


RiskDefinitionCreateResponse.model_rebuild()

__all__ = [
    "RiskCatalogResponse",
    "RiskDefinitionCreateResponse",
    "RiskDefinitionListResponse",
    "RiskDefinitionRead",
    "RiskDistributionRequest",
    "RiskDistributionResponse",
    "RiskPredictionRequest",
    "RiskPredictionResponse",
    "RiskVariableResponse",
    "RiskVersionResponse",
]
