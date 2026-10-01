"""Module 17 - controlled model deployment.

A training run produces a model. What this module does is make producing a model
and putting it into service two different events, between which a person has to
appear and say why.

**The lifecycle is experimental -> validated -> deployed -> retired, and only
forward.** ``experimental`` is what every training run writes, which is the whole
mechanism behind "never silently replace the deployed model after retraining": a
retrain cannot reach ``deployed`` because it does not write ``deployed``. A new
version is inert until somebody deploys it, and the deployed version keeps
answering requests in the meantime.

**Validation is where a human reads the report.** Nothing may be validated or
deployed without a stored evaluation, and the ``evaluation_id`` is recorded on the
history entry. This is what separates a decision from a schedule: an evaluation is
evidence, and the transition is somebody's judgement about that evidence.

**Replacing a deployed model is a two-part, acknowledged act.** A target may have
at most one deployed version, enforced by a unique partial index on
``model_versions`` filtered to ``status == "deployed"``. When a deployment would
displace an incumbent the request must carry ``supersede: true`` and a
``supersede_reason``; without them the service refuses with 409 and names the
incumbent. The service then retires the incumbent and writes both history entries,
so the substitution reads as one deliberate act rather than a row quietly losing
``deployed``.

**Nothing is inferred on the caller's behalf.** The version is named in the body,
never defaulted to the newest. The incumbent is named in the refusal. The response
echoes the evaluation the decision rests on. Each of those is a case where a
default would have been convenient and would have made the registry's state a
function of write order instead of of decisions.

The status is stored on the model version for cheap reads; the *history* of how it
got there is stored append-only in ``model_deployments``, because a current status
is the one thing that cannot show whether a change was made or merely happened.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from typing import Any

from app.db.collections import (
    MODEL_DEPLOYMENTS,
    MODEL_EVALUATIONS,
    MODEL_VERSIONS,
    RESEARCH_DATASETS,
)
from app.db.repository import MongoRepository
from app.ml.comparison import COMPARISON_COLUMNS, SELECTION_CRITERION
from app.ml.provenance import EVIDENCE_NOT_GENUINE_MESSAGE, verdict_from_parameters
from app.models import (
    ModelDeployment,
    ModelDeploymentAction,
    ModelEvaluation,
    ModelStatus,
    ModelVersion,
    ResearchDataset,
    ResearchTarget,
)
from app.models.base import utcnow

#: The refusal codes this module returns. Stable strings, so a client can branch on
#: a refusal rather than on a sentence.
NO_SUCH_MODEL = "no_such_model_version"
NOT_EVALUATED = "not_evaluated"
NON_EVIDENTIAL_MODEL = "non_evidential_model"
ILLEGAL_TRANSITION = "illegal_transition"
SUPERSEDE_UNACKNOWLEDGED = "supersede_unacknowledged"

#: The forward edges of the lifecycle. Anything not listed here cannot happen.
#: Expressed as a map rather than as a rank comparison so that a future status
#: added to the enum is refused by default instead of silently becoming
#: deployable.
ALLOWED_TRANSITIONS: dict[ModelStatus, tuple[ModelStatus, ...]] = {
    ModelStatus.EXPERIMENTAL: (ModelStatus.VALIDATED,),
    ModelStatus.VALIDATED: (ModelStatus.DEPLOYED,),
    ModelStatus.DEPLOYED: (ModelStatus.RETIRED,),
    ModelStatus.RETIRED: (),
}

#: The metrics the registry shows in its inspection view. Not restated here: the
#: registry reuses the comparison table's own column list, so a metric that Module
#: 09 decides is not a comparison column is not silently added to the registry's
#: row by this module inventing a second list. The readings themselves come from
#: ``ClassificationEvaluation.headline()``, which is Module 09's canonical
#: accessor -- this module never computes a metric of its own.
SUMMARY_METRICS: tuple[str, ...] = COMPARISON_COLUMNS


def _json_safe(value: Any) -> Any:
    """Recursively convert datetimes and enums to their JSON forms.

    Written rather than reached for with ``json.dumps(default=str)``, because a
    default hook would also stringify whatever else slipped in -- a whole nested
    model, say -- and a refusal body that renders half its contents as Python repr
    is a body nobody can parse on the client.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


#: Applied to the assembled body so the router can hand it straight to JSONResponse.
JSON_TYPES = _json_safe


class DeploymentRefusal(RuntimeError):
    """A lifecycle request that is refused, with the code and sentence to return.

    Distinct from a 404. A missing model version is a lookup failure; a refused
    transition is a decision the service made, and the reason belongs to the
    researcher as much as the code does.
    """

    def __init__(
        self,
        detail: str,
        *,
        code: str,
        model_id: str,
        version: str | None = None,
        status_code: int = 409,
        incumbent: ModelVersion | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.model_id = model_id
        self.version = version
        self.status_code = status_code
        self.incumbent = incumbent

    def as_body(self) -> dict[str, Any]:
        """The refusal body, JSON-safe.

        The incumbent is dumped with ``mode="json"`` rather than assembled field by
        field: its ``deployed_at`` is a datetime, and a refusal body that cannot be
        serialised is a 500 on the one error path a client needs to read.
        """
        body: dict[str, Any] = {
            "detail": self.detail,
            "refusal": self.code,
            "model_id": self.model_id,
        }
        if self.version is not None:
            body["version"] = self.version
        if self.incumbent is not None:
            body["deployed_incumbent"] = {
                "model_id": self.incumbent.model_id,
                "version": self.incumbent.version,
                "algorithm": self.incumbent.algorithm,
                "target": self.incumbent.target.value,
                "deployed_at": self.incumbent.deployed_at,
                "deployed_by": self.incumbent.deployed_by,
                "lifecycle_reason": self.incumbent.lifecycle_reason,
            }
        return JSON_TYPES(body)


class DeploymentService:
    """Inspect model versions and move them through the deployment lifecycle."""


    def __init__(self, database: Any) -> None:
        self._database = database

    # -- wiring ----------------------------------------------------------

    def _models(self) -> MongoRepository[ModelVersion]:
        return MongoRepository(self._database, MODEL_VERSIONS, ModelVersion)

    def _evaluations(self) -> MongoRepository[ModelEvaluation]:
        return MongoRepository(self._database, MODEL_EVALUATIONS, ModelEvaluation)

    def _history(self) -> MongoRepository[ModelDeployment]:
        return MongoRepository(self._database, MODEL_DEPLOYMENTS, ModelDeployment)

    async def build_record(
        self,
        model: ModelVersion,
        report: ModelEvaluation | None,
        *,
        dataset: ResearchDataset | None = None,
    ) -> ModelVersionRecord:
        """The HTTP record for one model version.

        Assembles the ten fields a deployment decision rests on, each read from
        where it is stored rather than reconstructed:

        * ``model_id`` and ``version`` name the model version;
        * ``dataset_id``, ``dataset_version`` and ``dataset_content_hash`` say
          which dataset revision it was fitted to;
        * ``trained_at`` is the training date, as the run recorded it;
        * ``feature_names`` are the features, in the order the frame was built;
        * ``hyperparameters`` is the stored ``parameters`` map;
        * ``evaluation_metrics`` is Module 09's ``headline()`` readings;
        * ``target_definition`` is the dataset's own target spec;
        * ``split_strategy`` and ``split_detail`` are how the held-out set was
          chosen;
        * ``status`` is where the version stands.

        ``target_definition`` falls back to the target's own name when the dataset
        record cannot be read, and says so in a ``note``. A registry that filled in
        a horizon or a label column it could not look up would be describing a
        target from a rule that is not in the file.
        """
        if dataset is None:
            dataset = await self._dataset_for(model)
        return ModelVersionRecord(
            model_id=model.model_id,
            version=model.version,
            status=model.status,
            target=model.target,
            algorithm=model.algorithm,
            framework=model.framework,
            dataset_id=model.dataset_id,
            dataset_version=model.dataset_version,
            dataset_content_hash=model.dataset_content_hash,
            trained_at=model.trained_at,
            feature_names=list(model.feature_names),
            hyperparameters=dict(model.parameters),
            training_metrics=dict(model.metrics),
            target_definition=_target_definition(model, dataset),
            split_strategy=model.split_strategy,
            split_detail=dict(model.split_detail),
            random_seed=model.random_seed,
            library_versions=dict(model.library_versions),
            artifact_path=model.artifact_path,
            training_duration_seconds=model.training_duration_seconds,
            validated_at=model.validated_at,
            validated_by=model.validated_by,
            deployed_at=model.deployed_at,
            deployed_by=model.deployed_by,
            retired_at=model.retired_at,
            retired_by=model.retired_by,
            lifecycle_reason=model.lifecycle_reason,
            superseded_by_model_id=model.superseded_by_model_id,
            superseded_by_version=model.superseded_by_version,
            evaluation=None if report is None else _evaluation_summary(report),
            available_transitions=[
                status.value for status in ALLOWED_TRANSITIONS.get(model.status, ())
            ],
            blocked_reason=None if report is not None or model.status is not ModelStatus.EXPERIMENTAL else (
                f"{NOT_EVALUATED} This version has no stored evaluation report, so "
                "it cannot be validated or deployed. Run POST /api/evaluation/run "
                "for it first."
            ),
        )

    async def _dataset_for(self, model: ModelVersion) -> ResearchDataset | None:
        """The dataset revision this model was fitted to, when it can be read.

        Matched on ``dataset_id`` *and* ``version``: a dataset id names a lineage
        whose later revisions are different tables, and the target spec of ``v3``
        is not evidence about the spec that ``v1`` was built under.
        """
        if model.dataset_id is None or model.dataset_version is None:
            return None
        found = await MongoRepository(
            self._database, RESEARCH_DATASETS, ResearchDataset
        ).find({"dataset_id": model.dataset_id, "version": model.dataset_version})
        return found[0] if found else None

    # -- inspection ------------------------------------------------------

    async def list_versions(
        self,
        *,
        target: ResearchTarget | None = None,
        status: ModelStatus | None = None,
        evaluated_only: bool = False,
    ) -> tuple[list[ModelVersion], dict[ModelVersion, ModelEvaluation | None]]:
        """Every model version, newest first, with its evaluation when one exists.

        Returns the versions and the report map together because a registry that
        showed a model without its report would send a researcher to the evaluation
        module to fetch figures this module already has, and the two answers could
        then disagree about which model is being described.
        """
        query: dict[str, Any] = {}
        if target is not None:
            query["target"] = target.value
        if status is not None:
            query["status"] = status.value
        versions = await self._models().find(query, sort=[("created_at", -1), ("model_id", 1)])
        reports = await self._reports_for(versions)
        if evaluated_only:
            versions = [v for v in versions if reports.get(v.model_id) is not None]
        return versions, reports

    async def get_version(self, model_id: str, version: str | None) -> ModelVersion:
        """One model version, or a refusal naming what was asked for.

        ``version`` may be omitted to read the newest version of a model. That is
        offered only on the read path: reading "show me this model" has a safe
        default, while writing "deploy this model" does not, so the two paths
        differ deliberately rather than by accident.
        """
        if version is None:
            found = await self._models().find(
                {"model_id": model_id}, sort=[("created_at", -1), ("version", -1)]
            )
            if not found:
                raise self._not_found(model_id, version)
            return found[0]
        return await self._load(model_id, version)

    async def history(self, model_id: str, version: str | None = None) -> list[ModelDeployment]:
        """The append-only deployment history, oldest first.

        Oldest first because a lifecycle is read forwards. The most recent entry
        is last, which is also the one most callers want; they can take ``[-1]``
        rather than reasoning about sort order.
        """
        query: dict[str, Any] = {"model_id": model_id}
        if version is not None:
            query["version"] = version
        entries = await self._history().find(query, sort=[("created_at", 1), ("deployment_id", 1)])
        return entries

    async def deployed_version(self, target: ResearchTarget) -> ModelVersion | None:
        """The one model version allowed to answer operational requests for a target.

        Module 10 calls this on every prediction. It is the single choke point for
        the rule "only a deployed model may predict", and it returns ``None``
        rather than raising so each caller decides what the absence means for it.
        """
        found = await self._models().find(
            {"target": target.value, "status": ModelStatus.DEPLOYED.value}, sort=[("deployed_at", -1)]
        )
        return found[0] if found else None

    async def deployed_versions(self) -> list[ModelVersion]:
        """Every deployed model version, one per target at most."""
        return await self._models().find(
            {"status": ModelStatus.DEPLOYED.value}, sort=[("target", 1), ("deployed_at", -1)]
        )

    # -- the transitions -------------------------------------------------

    async def validate(
        self,
        model_id: str,
        *,
        version: str,
        reason: str,
        actor: str,
    ) -> tuple[ModelVersion, ModelDeployment, ModelVersion | None]:
        """Accept a model's evaluation report, making it eligible for deployment.

        Refused without a report, because accepting an evaluation nobody computed
        is a rubber stamp. Refused for an already-deployed model, because a
        deployed model has already passed this step and re-running it would write
        a history entry that implies doubt about a model that is in service.
        """
        model = await self._load(model_id, version)
        self._require_evidential(model)
        report = await self.evaluation_for(model)
        self._require_evaluation(model, report, ModelStatus.VALIDATED)
        await self._require_transition(model, ModelStatus.VALIDATED)

        now = utcnow()
        model = await self._save(
            model.model_copy(
                update={
                    "status": ModelStatus.VALIDATED,
                    "validated_at": now,
                    "validated_by": actor,
                    "lifecycle_reason": reason,
                    "updated_at": now,
                }
            )
        )
        entry = await self._record(
            model=model,
            action=ModelDeploymentAction.VALIDATED,
            from_status=ModelStatus.EXPERIMENTAL,
            to_status=ModelStatus.VALIDATED,
            reason=reason,
            actor=actor,
            evaluation_id=None if report is None else report.evaluation_id,
        )
        return model, entry, None

    async def deploy(
        self,
        model_id: str,
        *,
        version: str,
        reason: str,
        actor: str,
        supersede: bool = False,
        supersede_reason: str | None = None,
    ) -> tuple[ModelVersion, ModelDeployment, ModelVersion | None]:
        """Put a validated model version into operational service.

        The incumbent check comes before anything is written. If another version
        of the same target is already deployed and the request has not
        acknowledged the substitution, nothing is modified and the caller is told
        which model they would have displaced. When the substitution is
        acknowledged, the incumbent is retired *first* and its history entry is
        written, because the unique partial index permits only one deployed
        version per target: retiring before deploying is what makes the order of
        these two writes observable rather than dependent on which write the
        database happened to accept.
        """
        model = await self._load(model_id, version)
        self._require_evidential(model)
        report = await self.evaluation_for(model)
        self._require_evaluation(model, report, ModelStatus.DEPLOYED)
        await self._require_transition(model, ModelStatus.DEPLOYED)

        incumbent = await self._incumbent_for(model)
        if incumbent is not None:
            if not supersede:
                raise DeploymentRefusal(
                    detail=(
                        f"{SUPERSEDE_UNACKNOWLEDGED} {incumbent.model_id} "
                        f"{incumbent.version} is already deployed for target "
                        f"{model.target.value} and is the model currently answering "
                        "operational requests. Deploying "
                        f"{model.model_id} {model.version} would replace it, which is "
                        "not something this module will do on its own. Resubmit with "
                        "supersede=true and a supersede_reason to make the replacement "
                        "deliberate: the incumbent will be retired with that reason "
                        "recorded against it, and both steps will appear in the "
                        "deployment history."
                    ),
                    code=SUPERSEDE_UNACKNOWLEDGED,
                    model_id=model.model_id,
                    version=model.version,
                    incumbent=incumbent,
                )
            if not supersede_reason:
                raise DeploymentRefusal(
                    detail=(
                        f"{SUPERSEDE_UNACKNOWLEDGED} The request asks to supersede "
                        f"{incumbent.model_id} {incumbent.version} but carries no "
                        "supersede_reason. Retiring a working model without recording "
                        "why is the silent replacement this module refuses to perform, "
                        "so the reason is required whenever a replacement is."
                    ),
                    code=SUPERSEDE_UNACKNOWLEDGED,
                    model_id=model.model_id,
                    version=model.version,
                    incumbent=incumbent,
                )

        now = utcnow()
        retired_incumbent: ModelVersion | None = None
        if incumbent is not None:
            retired_incumbent = await self._retire_incumbent(
                incumbent, superseded_by=model, reason=supersede_reason or "", actor=actor, now=now
            )

        model = await self._save(
            model.model_copy(
                update={
                    "status": ModelStatus.DEPLOYED,
                    "validated_at": model.validated_at or now,
                    "validated_by": model.validated_by or actor,
                    "deployed_at": now,
                    "deployed_by": actor,
                    "lifecycle_reason": reason,
                    "updated_at": now,
                }
            )
        )
        entry = await self._record(
            model=model,
            action=ModelDeploymentAction.DEPLOYED,
            from_status=ModelStatus.VALIDATED,
            to_status=ModelStatus.DEPLOYED,
            reason=reason,
            actor=actor,
            evaluation_id=None if report is None else report.evaluation_id,
            superseded=retired_incumbent,
        )
        return model, entry, retired_incumbent

    async def retire(
        self,
        model_id: str,
        *,
        version: str,
        reason: str,
        actor: str,
    ) -> tuple[ModelVersion, ModelDeployment, ModelVersion | None]:
        """Withdraw a model version from service.

        A retirement leaves no deployed model for the target, and that is stated
        in the refusal for the next prediction rather than papered over: the
        alternative would be a stale model still answering because retiring it
        half-succeeded.
        """
        model = await self._load(model_id, version)
        await self._require_transition(model, ModelStatus.RETIRED)
        now = utcnow()
        model = await self._save(
            model.model_copy(
                update={
                    "status": ModelStatus.RETIRED,
                    "retired_at": now,
                    "retired_by": actor,
                    "lifecycle_reason": reason,
                    "updated_at": now,
                }
            )
        )
        entry = await self._record(
            model=model,
            action=ModelDeploymentAction.RETIRED,
            from_status=ModelStatus.DEPLOYED,
            to_status=ModelStatus.RETIRED,
            reason=reason,
            actor=actor,
        )
        return model, entry, None

    # -- internals -------------------------------------------------------

    async def _load(self, model_id: str, version: str) -> ModelVersion:
        found = await self._models().find({"model_id": model_id, "version": version})
        if not found:
            raise self._not_found(model_id, version)
        return found[0]

    async def _save(self, model: ModelVersion) -> ModelVersion:
        return await self._models().replace("model_id", model.model_id, model)

    async def _incumbent_for(self, model: ModelVersion) -> ModelVersion | None:
        """The deployed version of the same target, if there is one.

        Excludes the model being deployed, so re-deploying is never treated as a
        self-replacement. The status filter is the same one the unique partial
        index enforces, so this query and the index cannot disagree.
        """
        found = await self._models().find(
            {
                "target": model.target.value,
                "status": ModelStatus.DEPLOYED.value,
                "model_id": {"$ne": model.model_id},
            }
        )
        return found[0] if found else None

    async def _retire_incumbent(
        self,
        incumbent: ModelVersion,
        *,
        superseded_by: ModelVersion,
        reason: str,
        actor: str,
        now: Any,
    ) -> ModelVersion:
        retired = await self._save(
            incumbent.model_copy(
                update={
                    "status": ModelStatus.RETIRED,
                    "retired_at": now,
                    "retired_by": actor,
                    "lifecycle_reason": reason,
                    "superseded_by_model_id": superseded_by.model_id,
                    "superseded_by_version": superseded_by.version,
                    "updated_at": now,
                }
            )
        )
        await self._record(
            model=retired,
            action=ModelDeploymentAction.RETIRED,
            from_status=ModelStatus.DEPLOYED,
            to_status=ModelStatus.RETIRED,
            reason=reason,
            actor=actor,
        )
        return retired

    async def _record(
        self,
        *,
        model: ModelVersion,
        action: ModelDeploymentAction,
        from_status: ModelStatus,
        to_status: ModelStatus,
        reason: str,
        actor: str,
        evaluation_id: str | None = None,
        superseded: ModelVersion | None = None,
    ) -> ModelDeployment:
        """Append one deployment history document.

        The selection criterion is copied from the evaluation at the moment of the
        decision rather than looked up on read, so a later re-evaluation cannot
        change what this decision was recorded as resting on.
        """
        entry = ModelDeployment(
            model_id=model.model_id,
            version=model.version,
            target=model.target,
            action=action,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            actor=actor,
            evaluation_id=evaluation_id,
            selection_criterion=SELECTION_CRITERION,
            superseded_model_id=None if superseded is None else superseded.model_id,
            superseded_version=None if superseded is None else superseded.version,
            notes={"algorithm": model.algorithm, "target": model.target.value},
        )
        return await self._history().insert(entry)

    async def evaluation_for(self, model: ModelVersion) -> ModelEvaluation | None:
        """The stored evaluation for exactly this version.

        Matched on ``(model_id, version)`` rather than on ``model_id``. A report
        for ``v1.0.0`` says nothing about ``v1.1.0``, and treating it as though it
        did is the failure that would let an unevaluated model into service.
        """
        found = await self._evaluations().find(
            {"model_id": model.model_id, "version": model.version}
        )
        return found[0] if found else None

    async def _reports_for(
        self, versions: Sequence[ModelVersion]
    ) -> dict[str, ModelEvaluation | None]:
        reports: dict[str, ModelEvaluation | None] = {}
        for model in versions:
            reports[model.model_id] = await self.evaluation_for(model)
        return reports

    def _require_evidential(self, model: ModelVersion) -> None:
        """Refuse to validate or deploy a model whose recorded evidence is not genuine.

        The evaluation layer refuses non-genuine rows before it will write a
        report, so a report's existence usually settles this. It is re-checked
        here anyway, for the same reason leakage is re-checked at train time
        (Module 17, ``_check_leakage``): the evaluation gate trusts the model's
        stored ``parameters`` on the day it runs, and a registry document can be
        altered or restored after that day. Deployment is the step that puts a
        number into operational service, so the evidence the whole lifecycle
        rests on is read off the version at the moment it is asked to serve --
        not assumed from a report that was written earlier against a record that
        may since have changed.
        """
        verdict = verdict_from_parameters(model.parameters)
        if verdict.is_evidential:
            return
        raise DeploymentRefusal(
            detail=(
                f"{NON_EVIDENTIAL_MODEL} {model.model_id} {model.version} will not be "
                "put into service on the evidence its version currently records: "
                f"{verdict.refusal_reason() or EVIDENCE_NOT_GENUINE_MESSAGE} A "
                "deployment is a claim that the model answers an operational "
                "question with the performance its report measures, and that claim "
                "cannot rest on training rows that are not genuine measurements."
            ),
            code=NON_EVIDENTIAL_MODEL,
            model_id=model.model_id,
            version=model.version,
        )

    def _require_evaluation(
        self,
        model: ModelVersion,
        report: ModelEvaluation | None,
        wanted: ModelStatus,
    ) -> None:
        if report is not None:
            return
        raise DeploymentRefusal(
            detail=(
                f"{NOT_EVALUATED} {model.model_id} {model.version} has no stored "
                f"evaluation report, so it cannot be moved to {wanted.value}. Run "
                "POST /api/evaluation/run for this model first: deployment rests on a "
                "measured report, and a model nobody has measured has nothing to "
                "deploy on."
            ),
            code=NOT_EVALUATED,
            model_id=model.model_id,
            version=model.version,
        )

    async def _require_transition(self, model: ModelVersion, wanted: ModelStatus) -> None:
        """Refuse a transition the lifecycle does not allow.

        Checked here as well as by the stored document's own validators, because
        the validators only see the status they are given: a client asking to
        deploy a retired model is a request this module should refuse with an
        explanation, not a write that fails on a field constraint.
        """
        allowed = ALLOWED_TRANSITIONS.get(model.status, ())
        if wanted in allowed:
            return
        reachable = _reachable_explanation(model)
        raise DeploymentRefusal(
            detail=(
                f"{ILLEGAL_TRANSITION} {model.model_id} {model.version} is "
                f"{model.status.value}, and the only forward step from "
                f"{model.status.value} is "
                f"{', '.join(s.value for s in allowed) if allowed else 'none — the status is terminal'}. "
                f"It cannot be moved to {wanted.value}. {reachable}"
            ),
            code=ILLEGAL_TRANSITION,
            model_id=model.model_id,
            version=model.version,
        )

    @staticmethod
    def _not_found(model_id: str, version: str | None) -> DeploymentRefusal:
        asked = f"{model_id} {version}" if version else model_id
        return DeploymentRefusal(
            detail=(
                f"{NO_SUCH_MODEL} No model version {asked} is in the registry. "
                "Nothing is deployed or changed by this request."
            ),
            code=NO_SUCH_MODEL,
            model_id=model_id,
            version=version,
            status_code=404,
        )


from app.schemas.registry import EvaluationSummary, ModelVersionRecord


def _evaluation_summary(report: ModelEvaluation) -> EvaluationSummary:
    """Module 09's headline readings, re-exported without alteration.

    The metrics are the stored reading objects, not numbers extracted from them.
    A reading that was not computed keeps ``value: null``, ``computed: false`` and
    its reason, so this module cannot turn an absent metric into a zero on its way
    to the registry screen. ``absent_metrics`` names them separately so a client
    can show "not calculated" without inspecting every cell.
    """
    readings = report.evaluation.headline()
    return EvaluationSummary(
        evaluation_id=report.evaluation_id,
        computed=True,
        evaluated_at=report.evaluated_at,
        dataset_id=report.dataset_id,
        dataset_version=report.dataset_version,
        evaluated_rows=report.evaluated_rows,
        metrics={name: reading.model_dump() for name, reading in readings.items()},
        absent_metrics=sorted(name for name, r in readings.items() if not r.computed),
        has_cross_validation=report.cross_validation.performed,
        per_class_count=len(report.evaluation.per_class),
        selection_criterion=SELECTION_CRITERION,
    )


def _target_definition(
    model: ModelVersion, dataset: ResearchDataset | None
) -> dict[str, Any]:
    """The target spec, from the dataset that defined it.

    Read from the dataset rather than restated here. For a risk target the spec
    carries the risk rule's id, version and specification hash, and those are the
    fields that let somebody re-derive the labels a model was fitted to; a
    registry that reported only "operational_risk" would make an unresolvable
    target look like a described one.
    """
    if dataset is not None:
        return dataset.target.model_dump(mode="json")
    return {
        "name": model.target.value,
        "note": (
            f"The dataset record for {model.dataset_id} version "
            f"{model.dataset_version} could not be read, so the full target "
            "specification is not shown here. The name above is the only part of "
            "the target definition this module can state from the model version "
            "alone, and the missing fields are not filled in."
        ),
    }


def _reachable_explanation(model: ModelVersion) -> str:
    """Say what the researcher can do next, in terms of the model's own state."""
    if model.status is ModelStatus.RETIRED:
        return (
            "Retired is terminal: a retired version is not re-deployed, because "
            "reopening a settled question is how a model returns to service without "
            "being looked at again. Retrain it and evaluate the new version."
        )
    if model.status is ModelStatus.DEPLOYED:
        return "It is already in service; retire it before deploying a replacement."
    if model.status is ModelStatus.VALIDATED:
        return "It is already validated and awaiting deployment."
    return "Validate it first, once an evaluation report exists."


__all__ = [
    "ALLOWED_TRANSITIONS",
    "ILLEGAL_TRANSITION",
    "NOT_EVALUATED",
    "NO_SUCH_MODEL",
    "SUMMARY_METRICS",
    "SUPERSEDE_UNACKNOWLEDGED",
    "DeploymentRefusal",
    "DeploymentService",
]
