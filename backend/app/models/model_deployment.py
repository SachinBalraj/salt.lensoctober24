"""The append-only record of a model version's deployment lifecycle.

A model's current status is denormalised onto :class:`~app.models.model_version.ModelVersion`
so that "which model is deployed?" is one indexed query. That denormalisation is
useful precisely because it is lossy: it records where a model ended up and
nothing about how it got there, and a model that was retired silently by a
retraining run is indistinguishable from one retired deliberately.

This collection exists so that is not possible. Every status change writes one
document here, and each one carries the status it moved from, the status it moved
to, who asked, why, and which version it displaced. A deployment that nobody
chose leaves no such document, and the absence is detectable.
"""

from typing import Any

from pydantic import Field, field_validator, model_validator

from app.models.base import (
    BaseDocument,
    UtcDateTime,
    new_id,
    normalize_identifier,
    utcnow,
)
from app.models.enums import ModelDeploymentAction, ModelStatus, ResearchTarget


class ModelDeployment(BaseDocument):
    """One step of one model version's deployment history.

    Never updated and never deleted. The model version carries the current state;
    this carries the path to it.
    """

    deployment_id: str = Field(
        default_factory=lambda: new_id("deploy"),
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    model_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    version: str = Field(min_length=1, max_length=64, pattern=r"^v?\d+\.\d+\.\d+$")
    target: ResearchTarget
    action: ModelDeploymentAction
    from_status: ModelStatus
    to_status: ModelStatus = Field(
        description="The status the model version holds once this step is applied."
    )
    reason: str = Field(
        min_length=1,
        max_length=1000,
        description=(
            "Why this change was made, in the words of whoever made it. Required "
            "and non-empty rather than optional: a lifecycle change nobody can "
            "account for is the silent replacement this record exists to prevent. "
            "'Deployed for the June season' is acceptable; 'deployed' is not, "
            "because it says the transition happened but not that anybody decided it."
        ),
    )
    actor: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "Who made the change. SaltLens has no user accounts wired into this "
            "path yet, so this is a recorded claim rather than an authenticated "
            "identity; it is stored so the gap is visible in the history instead "
            "of being invisible because nobody asked."
        ),
    )
    #: The evaluation the decision rests on. Required for ``validated`` and
    #: ``deployed`` steps, so a model cannot be promoted on a score nobody
    #: computed. Null only for ``retired``, which needs no score to be justified.
    evaluation_id: str | None = Field(default=None, max_length=80)
    #: What this deployment displaced, when it displaced anything. Set on the
    #: ``deployed`` step that took over from an incumbent, and on the incumbent's
    #: own ``retired`` step, so the pair reads as one deliberate substitution
    #: rather than two unrelated edits.
    superseded_model_id: str | None = Field(default=None, max_length=64)
    superseded_version: str | None = Field(default=None, max_length=64, pattern=r"^v?\d+\.\d+\.\d+$")
    #: The criterion the decision used, copied from the evaluation at the moment
    #: of the decision. Stored rather than looked up later so a later re-run of
    #: the evaluation cannot change what the deployment claimed to rest on.
    selection_criterion: str | None = Field(default=None, max_length=1000)
    notes: dict[str, Any] = Field(default_factory=dict)
    created_at: UtcDateTime = Field(default_factory=utcnow)

    @field_validator("model_id", "superseded_model_id", mode="before")
    @classmethod
    def _normalize_identifiers(cls, value):
        if value is None:
            return None
        return normalize_identifier(value)

    @model_validator(mode="after")
    def _validate_step(self) -> "ModelDeployment":
        """Refuse a history entry that could not have happened.

        Three rules, each closing a way the record could otherwise read as a
        decision that was never made: a step that does not change the status is
        not a step; a validation or deployment with no evaluation behind it has
        no basis on record; and a substitution has to name both sides of it.
        """
        if self.from_status is self.to_status:
            raise ValueError(
                f"a {self.action.value} step from {self.from_status.value} to "
                f"{self.to_status.value} changes nothing; a model version is not "
                "written a history entry that does not move it"
            )
        if (
            self.action in (ModelDeploymentAction.VALIDATED, ModelDeploymentAction.DEPLOYED)
            and self.evaluation_id is None
        ):
            raise ValueError(
                f"a {self.action.value} step needs the evaluation_id it rests "
                "on. A model version cannot be validated or deployed against a "
                "score that was never calculated."
            )
        if bool(self.superseded_model_id) != bool(self.superseded_version):
            raise ValueError(
                "superseded_model_id and superseded_version are stored together or "
                "not at all; half a substitution is not a record of one"
            )
        if self.superseded_model_id is not None and self.action is not ModelDeploymentAction.DEPLOYED:
            raise ValueError(
                "only a deployed step can displace another model version"
            )
        return self
