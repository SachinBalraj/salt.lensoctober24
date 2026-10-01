from typing import Any

from pydantic import Field, field_validator, model_validator

from app.models.base import (
    BaseDocument,
    UtcDateTime,
    new_id,
    normalize_identifier,
    utcnow,
)
from app.models.enums import ModelStatus, ResearchTarget


class ModelVersion(BaseDocument):
    model_id: str = Field(
        default_factory=lambda: new_id("model"),
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description=(
            "The model's name in the registry: the identity that survives a "
            "retrain, so v1 and v2 of the same model share it."
        ),
    )
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description=(
            "An optional human label for the model, set by the researcher at "
            "training time. Purely for display: nothing resolves a model by it, and "
            "a prediction reports it alongside `model_id` rather than instead of it, "
            "so a label can never stand in for the identity that was deployed."
        ),
    )
    version: str = Field(min_length=1, max_length=64, pattern=r"^v?\d+\.\d+\.\d+$")
    target: ResearchTarget = Field(
        description=(
            "The research target this model predicts. Named as a ResearchTarget "
            "rather than a bare field, because 'salinity' is not a prediction "
            "target until somebody says which salinity, taken when, relative to "
            "the state being predicted."
        )
    )
    status: ModelStatus = Field(
        default=ModelStatus.EXPERIMENTAL,
        description=(
            "Where this version stands in the deployment lifecycle. Denormalised "
            "here so 'which model is deployed?' is one indexed query; the path to "
            "this status is in the model_deployments collection. A training run "
            "always writes EXPERIMENTAL, which is what makes a retrained model "
            "incapable of displacing the deployed one on its own."
        ),
    )
    algorithm: str | None = Field(default=None, min_length=1, max_length=128)
    framework: str | None = Field(default=None, min_length=1, max_length=128)
    dataset_id: str | None = Field(default=None, max_length=64)
    artifact_path: str | None = Field(default=None, max_length=512)
    parameters: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    test_size: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False)
    created_at: UtcDateTime = Field(default_factory=utcnow)
    updated_at: UtcDateTime = Field(default_factory=utcnow)
    trained_at: UtcDateTime | None = None

    # -- reproducibility -------------------------------------------------
    # Everything needed to say, years later, exactly what was fitted to what.
    # A metric without these is a number that cannot be re-derived, and a model
    # whose provenance cannot be re-derived cannot be defended either.

    random_seed: int | None = Field(
        default=None,
        ge=0,
        description=(
            "The seed handed to every stochastic step. Null only for a model "
            "fitted without one, which is recorded rather than assumed absent. "
            "Non-negative because a negative seed is not a seed; accepting one "
            "would record a value no library had actually been given."
        ),
    )
    dataset_content_hash: str | None = Field(
        default=None,
        max_length=80,
        description=(
            "The exact dataset revision this was fitted to. dataset_id names a "
            "lineage; this names a state of the records."
        ),
    )
    dataset_version: int | None = Field(default=None, ge=1)
    feature_names: list[str] = Field(default_factory=list)
    split_strategy: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "How the held-out set was chosen: chronological, grouped_by_pan, "
            "random, or one of the combined forms."
        ),
    )
    split_detail: dict[str, Any] = Field(default_factory=dict)
    library_versions: dict[str, str] = Field(default_factory=dict)
    training_duration_seconds: float | None = Field(
        default=None, ge=0, allow_inf_nan=False
    )

    # -- deployment ------------------------------------------------------
    # Written only by Module 17, and only in response to an explicit request
    # naming a reason. Every one of these is null on a freshly trained model, and
    # that is the point: a model that has never been through a lifecycle is
    # visibly a model nobody has decided anything about.

    validated_at: UtcDateTime | None = None
    validated_by: str | None = Field(default=None, max_length=128)
    deployed_at: UtcDateTime | None = None
    deployed_by: str | None = Field(default=None, max_length=128)
    retired_at: UtcDateTime | None = None
    retired_by: str | None = Field(default=None, max_length=128)
    #: The reason recorded for the most recent lifecycle change. Kept on the row
    #: as well as in the history so the registry listing can show why a model is
    #: deployed without a second query per row, and so the reason cannot be lost
    #: if the history is ever truncated.
    lifecycle_reason: str | None = Field(default=None, max_length=1000)
    #: The model version that took over from this one, set when this version is
    #: retired by a replacement. Points forward to the substitution so a reader
    #: holding only the retired row can still see what replaced it. Absent on a
    #: retirement that displaced nothing, which is the ordinary case: a model is
    #: usually retired because it is no longer wanted, not because a successor
    #: overtook it.
    superseded_by_model_id: str | None = Field(default=None, max_length=64)
    superseded_by_version: str | None = Field(default=None, max_length=64, pattern=r"^v?\d+\.\d+\.\d+$")

    @model_validator(mode="before")
    @classmethod
    def _normalize_target(cls, value: Any) -> Any:
        """Accept the older bare-field target names as aliases of a research target.

        A document written before targets were namespaced named a field
        (``salinity``). Those are historical records and are not rewritten; they
        are read as the salinity target they meant, which is the only reading that
        makes them mean anything.
        """
        if not isinstance(value, dict):
            return value
        values = dict(value)
        if values.get("target") in {"salinity", "salinity_gl"}:
            values["target"] = ResearchTarget.POST_RAIN_SALINITY_G_L.value
        return values

    @field_validator("model_id", "dataset_id", "superseded_by_model_id", mode="before")
    @classmethod
    def _normalize_identifiers(cls, value):
        if value is None:
            return None
        return normalize_identifier(value)

    @model_validator(mode="after")
    def _validate_deployment_state(self) -> "ModelVersion":
        """Refuse a lifecycle state the registry could not have produced.

        The denormalised deployment fields exist to answer questions without a
        second query, which is only safe while they agree with ``status``. These
        checks are what stops a status from being written without the timestamp
        and the person behind it, so ``deployed`` always means "this version, at
        this instant, because this reason, and somebody chose it".
        """
        if self.status is ModelStatus.DEPLOYED:
            if self.deployed_at is None:
                raise ValueError(
                    "a deployed model version needs the instant it was deployed at; "
                    "a status with no timestamp cannot be audited and would let a "
                    "replacement happen without a visible moment"
                )
            if self.deployed_by is None:
                raise ValueError(
                    "a deployed model version needs to record who deployed it"
                )
        if self.status in (ModelStatus.VALIDATED, ModelStatus.DEPLOYED) and self.validated_at is None:
            raise ValueError(
                f"a {self.status.value} model version has been through "
                "validation, and validation is what records the moment the "
                "evaluation was accepted. Deploying without it would skip the "
                "step where a human reads the report."
            )
        if self.status is ModelStatus.RETIRED and self.retired_at is None:
            raise ValueError(
                "a retired model version needs the instant it was retired at, for "
                "the same reason a deployment does: a retirement nobody can date is "
                "indistinguishable from one that never happened"
            )
        if bool(self.superseded_by_model_id) != bool(self.superseded_by_version):
            raise ValueError(
                "superseded_by_model_id and superseded_by_version are stored "
                "together or not at all; half a substitution names no model"
            )
        if self.superseded_by_model_id is not None and self.status is not ModelStatus.RETIRED:
            raise ValueError(
                "only a retired model version can name its successor; a version "
                "that is still in service has not been superseded"
            )
        return self

    @model_validator(mode="after")
    def _validate_audit_times(self) -> "ModelVersion":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        if self.trained_at is not None and self.trained_at < self.created_at:
            raise ValueError("trained_at cannot be earlier than created_at")
        # A lifecycle instant cannot precede the model it describes: there is no
        # deployed model that finished training after deployment.
        for field_name in ("validated_at", "deployed_at", "retired_at"):
            instant = getattr(self, field_name)
            if instant is not None and instant < self.created_at:
                raise ValueError(f"{field_name} cannot be earlier than created_at")
        return self
