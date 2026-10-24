"""Operational risk prediction, gated on a deployed model version.

This is the one place in SaltLens where a model is asked about a pan that has not
happened yet, and it is deliberately narrow. The gate is the whole point:

    A prediction is produced by a model version whose status is ``deployed``, and
    by no other. Not by the highest-scoring version, not by the newest version, not
    by whichever version happens to match the target. There is no fallback to an
    earlier model and no default model, because both would be a substitution made
    without anybody deciding it.

The gate is enforced here rather than in the router so that it cannot be bypassed
by a future caller: :meth:`OperationalPredictionService.predict` resolves the
deployed version first, and there is no code path that reaches the estimator
without one in hand.

**The input is the current digital twin.** An operational question is about a pan as
it stands, so the features are read off the pan's twin state (Module 05) and from
nothing else: not from the request, and not re-read from the records a training row
was assembled from. The row handed to the estimator holds exactly the deployed
model's declared ``feature_names``, in that order, and a quantity the twin does not
carry is left absent and imputed by the preprocessor the model was fitted with.
See :mod:`app.services.twin_features` for why a gap stays a gap.

**The number is the model's own.** The probability returned is whatever
``predict_proba`` produced for the predicted class, unmodified. Nothing here
rounds it into a headline, rescales it, or supplies one when the estimator has
none, and there is no rule-based or heuristic path that can be reached when a model
is absent: with nothing deployed the request is refused.

The prediction is stored with the model id, the version, the deployment id, the
risk definition it was expressed in, and a content-addressed id for the twin state
it was made from. A risk score that cannot be traced to the model version, the rule
and the input state is not auditable, and an audit that cannot follow the number is
not an audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from fastapi import HTTPException

from app.db.collections import ML_PREDICTIONS, MODEL_DEPLOYMENTS, RESEARCH_DATASETS
from app.db.repository import MongoRepository
from app.ml.comparison import SELECTION_CRITERION
from app.ml.pipeline import NON_FEATURE_COLUMNS
from app.models import MLPrediction, ModelDeployment, ModelVersion, ResearchDataset
from app.models.digital_twin_state import DigitalTwinState
from app.models.enums import MlTarget, ResearchTarget
from app.services.artifact_paths import ArtifactPathError, resolve_artifact_path
from app.services.dataset_catalog import FEATURE_INDEX, POST_EVENT_COLUMNS
from app.services.dataset_versioning import content_hash
from app.services.datasets import DatasetService
from app.services.deployment import DeploymentService
from app.services.digital_twin import DigitalTwinService
from app.services.explain import ExplanationUnavailable, ShapExplanationService
from app.services.ml import ARTIFACT_DIRECTORY, training_gate_summary
from app.services.twin_features import TwinFeatureAssembly, TwinFeatureRow

#: Refusal codes. Distinct from the deployment module's codes because these are
#: about a request rather than about a lifecycle: the same ``not_evaluated`` code
#: would mean two different things to a client.
NO_DEPLOYED_MODEL = "no_deployed_model"
MODEL_NOT_A_RISK_MODEL = "model_not_fitted_to_risk_rule"
RULE_UNAVAILABLE = "risk_rule_unavailable"
NO_TWIN_STATE = "no_twin_state"
NO_OBSERVED_FEATURES = "no_observed_features"
FEATURE_MISMATCH = "feature_mismatch"
ARTIFACT_UNAVAILABLE = "artifact_unavailable"
FUTURE_ONLY_FEATURE = "feature_not_knowable_at_prediction_time"

#: Why nothing is deployed for a target, reported beside the refusal.
#:
#: "no deployed model" is three different states with three different next
#: steps, and a single sentence forces the client to guess which one it is
#: looking at: a project that has never trained anything, a project whose model
#: is waiting for an evaluation, and a project whose evaluated model is waiting
#: for somebody to deploy it. The stage says which, so the interface can state
#: the requirement that actually unblocks it rather than a generic absence.
STAGE_NO_MODEL = "no_model"
STAGE_NOT_EVALUATED = "not_evaluated"
STAGE_NOT_DEPLOYED = "not_deployed"

#: The input a prediction is assembled from, named for the operator when it is
#: absent. Not a feature name, because it is not one: the twin is where every
#: feature comes from, and with no twin there is nothing to read a single one of
#: them from.
TWIN_STATE_INPUT = "digital twin state"

#: Whether Module 11 could attribute this prediction. Reported beside the answer
#: rather than inside it, because an explanation is additional to the model's output
#: and must not be mistaken for part of it.
EXPLANATION_AVAILABLE = "available"
EXPLANATION_UNAVAILABLE = "unavailable"
EXPLANATION_FAILED = "shap_computation_failed"


class PredictionRefusal(RuntimeError):
    """An operational prediction that is refused, with its code and sentence.

    Every one of these is a case where the honest answer is "no". A risk score
    from a model that is not deployed, from inputs assembled differently from its
    training rows, or from a rule that no longer resolves, is a number that would
    be acted on, so the request is refused and the reason is specific enough to act on.
    """

    def __init__(
        self,
        detail: str,
        *,
        code: str,
        model_id: str | None = None,
        stage: str | None = None,
        missing: list[str] | None = None,
        training_gate: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.model_id = model_id
        #: Which step is outstanding, for a ``no_deployed_model`` refusal.
        self.stage = stage
        #: The named inputs that could not be read, for a refusal about inputs.
        self.missing = missing
        #: The sufficiency gate's own counts, when training is the blocker.
        self.training_gate = training_gate

    def as_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"detail": self.detail, "refusal": self.code}
        if self.model_id is not None:
            body["model_id"] = self.model_id
        if self.stage is not None:
            body["stage"] = self.stage
        if self.missing:
            body["missing"] = list(self.missing)
        if self.training_gate is not None:
            body["training_gate"] = self.training_gate
        return body


@dataclass(frozen=True)
class ScoreOutcome:
    """What the estimator said about one row.

    ``probabilities`` is ``None`` when the artifact's estimator has no
    ``predict_proba``. That is a different thing from a probability of zero, and
    the two are kept apart here so that neither can be reported as the other.
    """

    classes: list[str]
    probabilities: list[float] | None
    predicted: str


def _rule_provenance(risk: Any) -> dict[str, Any]:
    """The rule the class vocabulary comes from, carried in the input snapshot.

    The snapshot records the state the model was asked about, and the class it
    returns is a name drawn from this rule's vocabulary. A reader holding only the
    snapshot can therefore tell which rule decided what the labels mean, which is
    the difference between "high" as a class and "high" as a word.
    """
    return {
        "risk_definition_id": risk.definition_id,
        "risk_definition_version": risk.version,
        "risk_specification_hash": risk.specification_hash,
        "risk_classes": [str(item.name) for item in risk.classes],
    }


class OperationalPredictionService:
    """Answer operational risk questions using the deployed model, or refuse."""

    def __init__(
        self,
        database: Any,
        *,
        deployment: DeploymentService | None = None,
        twin: DigitalTwinService | None = None,
        assembly: TwinFeatureAssembly | None = None,
        explainer: ShapExplanationService | None = None,
        artifact_directory: Path | None = None,
    ) -> None:
        self._database = database
        self._artifact_directory = artifact_directory or ARTIFACT_DIRECTORY
        self._deployments = deployment or DeploymentService(database)
        self._datasets = DatasetService(database)
        self._twins = twin or DigitalTwinService(database)
        self._assembly = assembly or TwinFeatureAssembly()
        # Module 11, so a prediction is attributed as it is made. Substituted in
        # tests; the same service the explain endpoints use, so a prediction made
        # over HTTP and one read back are explained by one implementation.
        self._explainer = explainer or ShapExplanationService(database)

    async def predict(
        self, *, pan_id: str, as_of: datetime | None = None, target: ResearchTarget | None = None
    ) -> dict[str, Any]:
        """Predict operational risk for one pan from its current twin state.

        The sequence is deliberate and not reordered for convenience:

        1. resolve the deployed model -- if there is none, stop;
        2. load the risk rule it was fitted to -- if it will not resolve, stop;
        3. read the pan's twin state -- if there is none, stop;
        4. assemble the model's exact feature list from that twin;
        5. load the registered artifact and score it;
        6. store the prediction with the provenance it will be read back with.

        Every step refuses rather than approximating, and step 1 cannot be skipped
        by any caller because this method is the only route to the estimator.
        """
        resolved_target = target or ResearchTarget.OPERATIONAL_RISK
        model = await self._deployments.deployed_version(resolved_target)
        if model is None:
            stage = await self._absence_stage(resolved_target)
            raise PredictionRefusal(
                detail=(
                    f"{NO_DEPLOYED_MODEL} No model version is deployed for target "
                    f"{resolved_target.value}, so this module will not predict. "
                    "Operational prediction is answered only by a model version whose "
                    "status is deployed, and no other. Train, evaluate and explicitly "
                    "deploy a version through Module 17 "
                    "(POST /api/models/{model_id}/deploy) and this endpoint will answer "
                    "for it. No rule-based or heuristic score is offered in its place."
                ),
                code=NO_DEPLOYED_MODEL,
                stage=stage,
                # The gate only speaks when training is what is missing. A model
                # that exists and was never deployed is not blocked by data, and
                # quoting a sufficiency audit beside it would point the reader at
                # the wrong remedy.
                training_gate=(
                    await training_gate_summary(self._database)
                    if stage == STAGE_NO_MODEL
                    else None
                ),
            )

        risk = await self._rule_of(model)
        twin = await self._twin_for(model, pan_id, as_of)
        row = self._row_for(model, twin)

        score = self._score(model, row.values)
        class_order = list(score.classes)
        predicted = score.predicted
        probabilities = score.probabilities
        if probabilities is not None:
            by_class = dict(zip(class_order, probabilities, strict=True))
            probability: float | None = by_class[predicted]
        else:
            # No number is invented here. The response says the model reported no
            # probabilities, and a reader who needs one has a model to deploy that
            # produces them.
            by_class = {}
            probability = None

        defined = [name for name in class_order if name in {c.name for c in risk.classes}]
        if predicted not in defined:
            # The estimator can only emit classes it was fitted on, and the rule
            # declares its own. A label outside the rule's vocabulary means the two
            # no longer describe the same problem, which is worth refusing rather
            # than mapping to a neighbouring class.
            raise PredictionRefusal(
                detail=(
                    f"{MODEL_NOT_A_RISK_MODEL} The deployed model predicted "
                    f"{predicted!r}, which is not a class declared by risk definition "
                    f"{risk.definition_id} version {risk.version}. The model's label "
                    "space and the rule's have diverged, so the output cannot be "
                    "expressed in the rule's vocabulary and no risk class is reported."
                ),
                code=MODEL_NOT_A_RISK_MODEL,
                model_id=model.model_id,
            )

        # The snapshot is the *input*: the twin state, the exact feature row built
        # from it, where each column came from, and the rule whose vocabulary the
        # classes are drawn from. The predicted class and the probability are
        # outputs and are stored as their own fields, so that hashing the snapshot
        # gives an id for the input alone and the two cannot be confused.
        snapshot = {
            **row.snapshot(),
            **_rule_provenance(risk),
            "produced_by": "operational_prediction",
        }
        input_snapshot_id = content_hash(snapshot)
        stored = await self._store(
            model=model,
            pan_id=twin.pan_id,
            predicted=predicted,
            probability=probability,
            snapshot=snapshot,
            input_snapshot_id=input_snapshot_id,
        )
        # Module 11 attributes the stored row to the model that read it, so every
        # prediction is explained as it is made rather than on request. The
        # explanation is an *addition* to the answer, never a condition of it: a
        # failure here leaves the model's own probability exactly as it was and
        # reports explainability as unavailable with its reason.
        _, explainability = await self._explain(stored)
        return {
            "prediction_id": stored.prediction_id,
            # The twin's spelling of the pan, not the request's, so the id in the
            # response is the id the prediction is stored under and can be queried
            # by. A request may spell it either way; the record spells it one way.
            "pan_id": stored.pan_id,
            "predicted_at": stored.created_at,
            "as_of": twin.as_of,
            "risk_class": predicted,
            "probability": probability,
            "class_probabilities": by_class,
            "defined_classes": defined,
            "model_id": model.model_id,
            "model_name": model.name,
            "model_version": model.version,
            "algorithm": model.algorithm,
            "deployment_id": stored.deployment_id,
            "deployed_at": model.deployed_at,
            "input_snapshot_id": input_snapshot_id,
            "twin_id": twin.twin_id,
            "twin_as_of": twin.as_of,
            "risk_definition_id": risk.definition_id,
            "risk_definition_version": risk.version,
            "dataset_id": model.dataset_id,
            "dataset_version": model.dataset_version,
            "selection_criterion": SELECTION_CRITERION,
            "features_used": sorted(row.observed_features),
            "features_absent": sorted(row.missing_features),
            "features_imputed": sorted(row.missing_features),
            "used_physics_prediction": row.used_physics,
            "input_source": "current_digital_twin_state",
            **explainability,
        }

    async def _absence_stage(self, target: ResearchTarget) -> str:
        """Which step is outstanding, when nothing is deployed for this target.

        Read from the same registry the gate itself reads, so the stage cannot
        disagree with the refusal: the versions are filtered by exactly the
        target that just failed to resolve, and a report is looked up per
        version the same way Module 09 stores it.

        The three states are ordered by how far the project got, not by
        severity. Nothing registered means training has not happened; registered
        without a report means evaluation has not happened; a report with no
        deployment means somebody has to decide to field it. A registry that
        cannot be listed raises rather than reporting "no model", because
        "nobody trained one" and "I could not read the registry" are different
        facts and only one of them is about the project.

        The evaluation is read per version rather than from ``list_versions``'
        report map, which is keyed by model id and so keeps only one version's
        report: a model whose v1 was evaluated and whose v2 was not has still
        been evaluated, and calling it unevaluated would send the reader to
        Module 09 for a report that already exists.
        """
        versions, _ = await self._deployments.list_versions(target=target)
        if not versions:
            return STAGE_NO_MODEL
        for version in versions:
            if await self._deployments.evaluation_for(version) is not None:
                return STAGE_NOT_DEPLOYED
        return STAGE_NOT_EVALUATED

    async def _explain(self, stored: MLPrediction) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Attribute the stored prediction, and report whether that was possible.

        The stored row is the input snapshot, so the attribution is about the same
        row that produced the number rather than about a row rebuilt from the twin
        after the fact. Everything that can go wrong here -- an artifact that is not
        on this host, a model with no reference distribution, a class-only estimator,
        a shap build that refuses the model -- arrives as a named reason, and the
        response says explainability is unavailable instead of returning a
        contribution that was not computed.

        A broad catch is deliberate. A SHAP failure must never cost the caller the
        model's own answer: the probability was computed before this was called, it
        is the answer, and a prediction that refuses because its explanation failed
        would be a worse system than one that explains what it can.
        """
        try:
            explanation, _ = await self._explainer.explain_prediction(
                stored.prediction_id, stored=stored
            )
        except ExplanationUnavailable as exc:
            return None, {
                "explanation_status": EXPLANATION_UNAVAILABLE,
                "explanation_refusal": exc.code,
                "explanation_refusal_detail": exc.detail,
            }
        except Exception as exc:  # noqa: BLE001 -- the prediction stands regardless
            return None, {
                "explanation_status": EXPLANATION_UNAVAILABLE,
                "explanation_refusal": EXPLANATION_FAILED,
                "explanation_refusal_detail": (
                    f"{EXPLANATION_FAILED} Module 11 could not attribute this "
                    f"prediction: {type(exc).__name__}: {exc}. The model's own class and "
                    "probability above are unaffected, and no attribution is reported."
                ),
            }
        return explanation, {
            "explanation_status": EXPLANATION_AVAILABLE,
            "explanation_id": explanation["explanation_id"],
            "explanation_refusal": None,
            "explanation_refusal_detail": None,
        }

    # -- inputs -----------------------------------------------------------

    async def _rule_of(self, model: ModelVersion) -> Any:
        """The risk rule the deployed model was fitted to, or a refusal.

        The dataset manifest names the rule by id and by specification hash, and
        both are re-checked here. The features themselves come from the twin; the
        rule is still needed, because the class this endpoint reports is a
        statement about *that rule's* vocabulary, and a model fitted to a measured
        regression target has no such statement to make.
        """
        dataset = await self._dataset_of(model)
        if dataset is None:
            raise PredictionRefusal(
                detail=(
                    f"{RULE_UNAVAILABLE} The deployed model {model.model_id} "
                    f"{model.version} records dataset "
                    f"{model.dataset_id!r} version {model.dataset_version}, which cannot "
                    "be read. Without that record there is no risk rule to express the "
                    "prediction in, and a class with no rule behind it is not a risk "
                    "class."
                ),
                code=RULE_UNAVAILABLE,
                model_id=model.model_id,
            )

        if dataset.target.requires_risk_definition is False:
            raise PredictionRefusal(
                detail=(
                    f"{MODEL_NOT_A_RISK_MODEL} The deployed model {model.model_id} "
                    f"{model.version} was fitted to the measured target "
                    f"{dataset.target.name.value}, not to a risk rule. A measured "
                    "regression or classification target is not an operational risk "
                    "class, so its output is not a risk score and will not be returned "
                    "as one."
                ),
                code=MODEL_NOT_A_RISK_MODEL,
                model_id=model.model_id,
            )

        try:
            risk = await self._datasets.risk_definition_for(dataset)
        except HTTPException as exc:
            # The rule lookup refuses when a manifest cites a rule that no longer
            # hashes to what it recorded. That is a refusal of the prediction too,
            # and the reason it gives is the reason there is no prediction.
            raise PredictionRefusal(
                detail=(
                    f"{RULE_UNAVAILABLE} The risk rule the deployed model "
                    f"{model.model_id} {model.version} was fitted to could not be "
                    f"resolved: {exc}. Nothing is predicted."
                ),
                code=RULE_UNAVAILABLE,
                model_id=model.model_id,
            ) from exc

        if risk is None:
            raise PredictionRefusal(
                detail=(
                    f"{MODEL_NOT_A_RISK_MODEL} The deployed model {model.model_id} "
                    f"{model.version} has no risk rule attached to its dataset, so it "
                    "predicts a target whose classes are not defined by a rule. An "
                    "operational risk prediction is a statement about a named rule's "
                    "class, and there is no class to state."
                ),
                code=MODEL_NOT_A_RISK_MODEL,
                model_id=model.model_id,
            )
        return risk

    async def _twin_for(
        self, model: ModelVersion, pan_id: str, as_of: datetime | None
    ) -> DigitalTwinState:
        """The twin state the prediction is made from.

        With no ``as_of`` this is the pan's current state, which is the ordinary
        operational question. With one, the newest state recorded at or before it,
        so a prediction anchored at a past instant is made from what the system
        believed then rather than from today's view of a past moment.

        A pan with no twin state has no operational input. This is refused rather
        than assembled from raw records: the twin is the system's current belief
        about the pan, and quietly building a substitute for it would be answering
        a different question than the one asked.
        """
        if as_of is None:
            try:
                twin = await self._twins.latest_snapshot(pan_id)
            except HTTPException as exc:
                raise PredictionRefusal(
                    detail=(
                        f"{NO_TWIN_STATE} Pan {pan_id!r} is not a registered pan, so it "
                        f"has no digital twin state to predict from: {exc}"
                    ),
                    code=NO_TWIN_STATE,
                    model_id=model.model_id,
                    missing=[TWIN_STATE_INPUT],
                ) from exc
            if twin is not None:
                return twin
            raise PredictionRefusal(
                detail=(
                    f"{NO_TWIN_STATE} Pan {pan_id!r} has no digital twin state, and the "
                    "features of an operational prediction are read from the twin. "
                    "Record a state for this pan through Module 05 "
                    "(POST /api/twin/{pan_id}/refresh) and the endpoint will answer. No "
                    "features are assembled from raw records in its place, because a "
                    "row the model has never seen the origin of is not the model's input."
                ),
                code=NO_TWIN_STATE,
                model_id=model.model_id,
                missing=[TWIN_STATE_INPUT],
            )

        try:
            _, snapshots, _ = await self._twins.list_history(pan_id, limit=200)
        except HTTPException as exc:
            raise PredictionRefusal(
                detail=(
                    f"{NO_TWIN_STATE} Pan {pan_id!r} is not a registered pan, so it has "
                    f"no digital twin state to predict from: {exc}"
                ),
                code=NO_TWIN_STATE,
                model_id=model.model_id,
                missing=[TWIN_STATE_INPUT],
            ) from exc
        eligible = [state for state in snapshots if state.as_of <= as_of]
        if not eligible:
            raise PredictionRefusal(
                detail=(
                    f"{NO_TWIN_STATE} Pan {pan_id!r} has no digital twin state recorded "
                    f"at or before {as_of.isoformat()}. The oldest state on record is "
                    f"{min((state.as_of for state in snapshots), default=None)}. A "
                    "prediction anchored earlier than the system's first belief about "
                    "the pan would be assembled from nothing."
                ),
                code=NO_TWIN_STATE,
                model_id=model.model_id,
                missing=[TWIN_STATE_INPUT],
            )
        return max(eligible, key=lambda state: state.as_of)

    def _row_for(self, model: ModelVersion, twin: DigitalTwinState) -> TwinFeatureRow:
        """The model's exact feature list, built from one twin state.

        Three things are refused before a row exists, each for a different reason
        and each reported as itself:

        * a declared feature that is a measurement taken *after* the anchor. A
          model can be fitted against such a column as its target and must never
          declare it as a feature, because an operational row is built before the
          measurement exists and would be filled with the answer.
        * a declared feature this module has no definition for. The assembly can
          only fill columns it knows the meaning of; an unknown name would arrive
          as absent on every row, so the model would be scoring a column of
          nothing.
        * a row in which nothing at all could be observed, which the caller reports
          separately.
        """
        names = list(model.feature_names)
        if not names:
            raise PredictionRefusal(
                detail=(
                    f"{FEATURE_MISMATCH} The deployed model {model.model_id} "
                    f"{model.version} records no features, so there is nothing to build "
                    "a row from. A model with an empty feature list is not a model that "
                    "predicts."
                ),
                code=FEATURE_MISMATCH,
                model_id=model.model_id,
            )

        post_event = [name for name in names if name in POST_EVENT_COLUMNS]
        if post_event:
            raise PredictionRefusal(
                detail=(
                    f"{FUTURE_ONLY_FEATURE} The deployed model {model.model_id} "
                    f"{model.version} declares {', '.join(post_event)} as a feature. "
                    "Those columns hold measurements taken after the anchor — a "
                    "dataset may be fitted *to* them as its target, but a prediction "
                    "is made before any of them exists, so a row built here could only "
                    "carry the answer. The model is not asked anything about this pan."
                ),
                code=FUTURE_ONLY_FEATURE,
                model_id=model.model_id,
            )

        unknown = [name for name in names if name not in FEATURE_INDEX]
        if unknown:
            raise PredictionRefusal(
                detail=(
                    f"{FEATURE_MISMATCH} The deployed model {model.model_id} "
                    f"{model.version} declares {', '.join(unknown)}, which is not a "
                    "feature this project defines. The twin supplies features by name "
                    "and a name with no definition behind it could only ever arrive "
                    "absent, so the estimator would be scoring a column of nothing."
                ),
                code=FEATURE_MISMATCH,
                model_id=model.model_id,
            )

        row = self._assembly.assemble(twin, names)
        if not row.observed_features:
            raise PredictionRefusal(
                detail=(
                    f"{NO_OBSERVED_FEATURES} None of the {len(names)} features the "
                    f"deployed model {model.model_id} {model.version} declares could be "
                    f"read from the digital twin state for pan {twin.pan_id!r} "
                    f"(twin {twin.twin_id}). Every input to this prediction would be "
                    "imputed, so the output would describe the training set's average "
                    "rather than this pan. The twin reports these gaps: "
                    + "; ".join(f"{name}: {why}" for name, why in sorted(row.absent.items())[:4])
                ),
                code=NO_OBSERVED_FEATURES,
                model_id=model.model_id,
                # The whole list, not the four the sentence quotes: a reader
                # fixing this needs every column that arrived empty, and the
                # detail only had room for the first few.
                missing=sorted(row.absent),
            )
        return row

    async def _dataset_of(self, model: ModelVersion) -> ResearchDataset | None:
        """The exact dataset revision the deployed model was fitted to.

        Matched on id *and* version. A dataset id names a lineage, and a rule read
        from a later revision of that lineage would be a different rule from the one
        the model's classes were defined by.
        """
        if model.dataset_id is None or model.dataset_version is None:
            return None
        found = await MongoRepository(
            self._database, RESEARCH_DATASETS, ResearchDataset
        ).find({"dataset_id": model.dataset_id, "version": model.dataset_version})
        return found[0] if found else None

    def _score(
        self, model: ModelVersion, features: dict[str, Any]
    ) -> ScoreOutcome:
        """Score one feature row with the registered artifact.

        The feature frame is assembled from the model's own ``feature_names`` in
        declared order, with the same exclusion list the training pipeline applies,
        so an identifier or a post-anchor column cannot reach the estimator through
        this path. Missing values are left missing: the stored preprocessor imputes
        them from the training rows, which is the imputation the model was fitted
        with, and inventing a value here would bypass it.

        The class comes from the estimator and the probability, when there is one,
        from ``predict_proba``. An estimator that offers no probabilities still has
        a class to give, so the class is returned with ``probabilities`` of ``None``
        rather than the whole prediction being withheld: the class is what the model
        said, and refusing to pass it on would replace a model's answer with this
        module's opinion about which models are allowed to answer.
        """
        try:
            path = resolve_artifact_path(
                self._artifact_directory, model.artifact_path or ""
            )
        except ArtifactPathError as exc:
            raise PredictionRefusal(
                detail=(
                    f"{ARTIFACT_UNAVAILABLE} The registry records an artifact path "
                    f"for {model.model_id} {model.version} that resolves outside the "
                    "artifact directory, so it is refused."
                ),
                code=ARTIFACT_UNAVAILABLE,
                model_id=model.model_id,
            ) from exc
        if not path.exists():
            raise PredictionRefusal(
                detail=(
                    f"{ARTIFACT_UNAVAILABLE} The registry records "
                    f"{model.artifact_path} for the deployed model {model.model_id} "
                    f"{model.version}, and no artifact is there on this host. A "
                    "prediction from a refitted model would not be from the deployed "
                    "model, so none is returned."
                ),
                code=ARTIFACT_UNAVAILABLE,
                model_id=model.model_id,
            )
        try:
            loaded = joblib.load(path)
        except Exception as exc:
            raise PredictionRefusal(
                detail=(
                    f"{ARTIFACT_UNAVAILABLE} The artifact at {path} could not be "
                    f"loaded: {exc}. The library versions this model was fitted with "
                    "are recorded on the model version."
                ),
                code=ARTIFACT_UNAVAILABLE,
                model_id=model.model_id,
            ) from exc

        estimator = loaded.get("estimator")
        preprocessing = loaded.get("preprocessing")
        classes = [str(label) for label in loaded.get("classes", [])]
        if estimator is None or preprocessing is None:
            raise PredictionRefusal(
                detail=(
                    f"{ARTIFACT_UNAVAILABLE} The file at {path} is not an artifact this "
                    "project wrote, so it is not scored here."
                ),
                code=ARTIFACT_UNAVAILABLE,
                model_id=model.model_id,
            )

        if not model.feature_names:
            raise PredictionRefusal(
                detail=(
                    f"{FEATURE_MISMATCH} The deployed model {model.model_id} "
                    f"{model.version} records no features, so there is nothing to "
                    "assemble a row from. A model with an empty feature list is not a "
                    "model that predicts."
                ),
                code=FEATURE_MISMATCH,
                model_id=model.model_id,
            )
        illegal = [name for name in model.feature_names if name in NON_FEATURE_COLUMNS]
        if illegal:
            raise PredictionRefusal(
                detail=(
                    f"{FEATURE_MISMATCH} The deployed model {model.model_id} "
                    f"{model.version} declares {', '.join(illegal)} as features, which "
                    "the pipeline excludes because they leak information unavailable at "
                    "prediction time. The feature list and the model disagree, so no "
                    "prediction is produced."
                ),
                code=FEATURE_MISMATCH,
                model_id=model.model_id,
            )

        frame = pd.DataFrame.from_records(
            [{name: features.get(name) for name in model.feature_names}]
        )
        frame = frame.reindex(columns=list(model.feature_names))
        transformed = preprocessing.transform(frame)

        if not hasattr(estimator, "predict_proba"):
            # A class with no number attached. Taken from the estimator's own
            # decision, not inferred from a rule, and reported as a class with no
            # probability rather than given one this module made up.
            emitted = estimator.predict(transformed)[0]
            if not classes:
                classes = [str(label) for label in getattr(estimator, "classes_", [])]
            return ScoreOutcome(classes=classes, probabilities=None, predicted=str(emitted))

        probabilities = [float(value) for value in estimator.predict_proba(transformed)[0]]
        if not classes:
            classes = [str(label) for label in getattr(estimator, "classes_", [])]
        if len(classes) != len(probabilities):
            raise PredictionRefusal(
                detail=(
                    f"{ARTIFACT_UNAVAILABLE} The artifact for the deployed model "
                    f"{model.model_id} {model.version} reports "
                    f"{len(probabilities)} probabilities for {len(classes)} classes, so "
                    "no class can be matched to its probability and none is guessed."
                ),
                code=ARTIFACT_UNAVAILABLE,
                model_id=model.model_id,
            )
        best = max(range(len(probabilities)), key=lambda index: probabilities[index])
        return ScoreOutcome(
            classes=classes, probabilities=probabilities, predicted=classes[best]
        )

    async def _store(
        self,
        *,
        model: ModelVersion,
        pan_id: str,
        predicted: str,
        probability: float | None,
        snapshot: dict[str, Any],
        input_snapshot_id: str,
    ) -> MLPrediction:
        """Store the prediction with the provenance needed to audit it later.

        The deployment id is looked up from the append-only history rather than
        synthesised, so a stored prediction points at the recorded decision that put
        this model in service. A prediction whose model was retired still points at
        the deployment it was made under, which is what makes a historical series
        readable after a replacement.

        The input snapshot is stored whole and addressed by the hash of its own
        contents, so the stored row and the stored id cannot disagree: two
        predictions from the same twin and the same model produce the same id, and a
        different row produces a different one.
        """
        entries: list[ModelDeployment] = await MongoRepository(
            self._database, MODEL_DEPLOYMENTS, ModelDeployment
        ).find(
            {
                "model_id": model.model_id,
                "version": model.version,
                "action": "deployed",
            },
            sort=[("created_at", -1)],
            limit=1,
        )
        record = MLPrediction(
            pan_id=pan_id,
            model_id=model.model_id,
            model_version=model.version,
            deployment_id=entries[0].deployment_id if entries else None,
            deployed_at=model.deployed_at,
            target=MlTarget.OPERATIONAL_RISK,
            timestamp=model.deployed_at or snapshot["twin_as_of"],
            # Left null: the field is typed for a regression value, and this is a
            # class. The class travels in ``predicted_class`` beside it.
            prediction=None,
            predicted_class=predicted,
            probability=probability,
            confidence=probability,
            input_snapshot_id=input_snapshot_id,
            input_snapshot=snapshot,
        )
        return await MongoRepository(
            self._database, ML_PREDICTIONS, MLPrediction
        ).insert(record)


__all__ = [
    "ARTIFACT_UNAVAILABLE",
    "EXPLANATION_AVAILABLE",
    "EXPLANATION_FAILED",
    "EXPLANATION_UNAVAILABLE",
    "FEATURE_MISMATCH",
    "FUTURE_ONLY_FEATURE",
    "MODEL_NOT_A_RISK_MODEL",
    "NO_DEPLOYED_MODEL",
    "NO_OBSERVED_FEATURES",
    "NO_TWIN_STATE",
    "RULE_UNAVAILABLE",
    "STAGE_NO_MODEL",
    "STAGE_NOT_DEPLOYED",
    "STAGE_NOT_EVALUATED",
    "TWIN_STATE_INPUT",
    "OperationalPredictionService",
    "PredictionRefusal",
]
