"""Module 10 - operational risk definitions, and the shape of their labels.

This module is about the *label*, not the prediction. It exists because a risk
model is only ever as defensible as the severity vocabulary it was fitted to, and
"high / medium / low" written into a training script is not a vocabulary anyone
can argue with: it cannot be revised without invalidating every model fitted to
it, and it cannot be cited as anything other than a constant.

So the vocabulary is data, held here. A researcher writes their own classes, their
own thresholds and their own reasons; this module stores them with a version, hands
them to the dataset builder as a rule, and reports what the rule's labels look like
across the stored records **before** anything is fitted.

Three absences are deliberate and load-bearing.

**`POST /predict` answers only from a deployed model version.** The endpoint exists
and is implemented, but it refuses with 409 unless a model version's status is
`deployed`, and it has no default model, no fallback to the newest version and no
approximate answer. The gate lives in the service rather than the handler, so no
other caller can reach the estimator around it. A plausible-looking risk score from
a model nobody chose to put in service is worse than an absent one, because it gets
acted on.

**There is no update or delete.** A rule that can be edited in place leaves every
model fitted to it undescribable. Changing a rule means posting a new version, and
`/definitions/{name}/versions` shows what changed.

**There are no default classes anywhere.** A researcher who posts a definition
without a class named "high" gets a module that has no opinion about severity, and
one who posts nothing gets no labels at all. That is the intended behaviour: the
class distribution of a built dataset is a fact about the researcher's rule and the
recorded pans, and never about a threshold chosen in this file.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import OptionalDbDependency, get_db
from app.api.routers.datasets import get_dataset_service
from app.db.errors import DocumentNotFoundError
from app.models.enums import ResearchTarget
from app.models.risk_definition import RiskDefinitionDraft
from app.schemas.risk import (
    RiskCatalogResponse,
    RiskDefinitionCreateResponse,
    RiskDefinitionListResponse,
    RiskDefinitionRead,
    RiskDistributionRequest,
    RiskDistributionResponse,
    RiskPredictionRequest,
    RiskPredictionResponse,
    RiskVariableResponse,
    RiskVersionResponse,
)
from app.services.dataset_catalog import TARGET_CATALOG
from app.services.datasets import DatasetService
from app.services.deployment import DeploymentService
from app.services.ml import training_gate_summary
from app.services.prediction import (
    STAGE_NO_MODEL,
    STAGE_NOT_DEPLOYED,
    STAGE_NOT_EVALUATED,
    OperationalPredictionService,
    PredictionRefusal,
)
from app.services.risk_definitions import (
    RiskDefinitionError,
    RiskDefinitionService,
)

router = APIRouter(prefix="/api/risk", tags=["10-risk"])

DESCRIPTION = (
    "Versioned operational risk criteria defined by a researcher against "
    "measurable variables, with the derived class distribution reported before any "
    "model is trained. Risk prediction is answered by the deployed model version "
    "and by nothing else: with none deployed this module refuses rather than "
    "approximating."
)

SERVICE_NOT_IMPLEMENTED = (
    "This module will not approximate a risk score. It lets a researcher define "
    "the criteria a model is fitted to, version them, and see how their labels "
    "fall across the stored records. A score appears only when a model version "
    "has been evaluated and then explicitly deployed through Module 17, and even "
    "then it is the deployed version that answers and no other."
)


def get_risk_definition_service() -> RiskDefinitionService:
    """Build the service with a lazy database handle.

    ``get_db`` is passed as a provider rather than called here so an unconfigured
    database raises its 503 inside the handler, after FastAPI has validated the
    request body. A malformed definition is a 422 whether or not Mongo is
    reachable.
    """
    return RiskDefinitionService(get_db)


ServiceDependency = Annotated[RiskDefinitionService, Depends(get_risk_definition_service)]
# The datasets router's own dependency, reused rather than redefined, so the
# distribution is produced by the same service a dataset build uses.
DatasetServiceDependency = Annotated[DatasetService, Depends(get_dataset_service)]


def get_prediction_service() -> OperationalPredictionService:
    """Build the operational prediction service with a lazy database handle.

    The same idiom as the definition service, so an unconfigured database raises
    its 503 inside the handler rather than at import time.
    """
    return OperationalPredictionService(get_db)


PredictionServiceDependency = Annotated[
    OperationalPredictionService, Depends(get_prediction_service)
]


async def _model_state(database: Any | None) -> tuple[bool | None, str]:
    """Whether a model exists for this module's target, and where it got to.

    Read from the same registry the prediction gate reads, so the status cannot
    claim an absence the gate would disagree with. The three "not in service"
    states are reported separately because they have three different remedies:
    train, evaluate, deploy.

    ``trained`` is ``None`` with ``model_state`` of ``unknown`` when the registry
    cannot be read. That distinction is the whole reason this is not a constant:
    reporting ``false`` for an unreachable database would put a claim about the
    project on screen that nobody made, and the client already treats a
    non-boolean ``trained`` as "this read did not answer".
    """
    if database is None:
        return None, "unknown"
    try:
        deployments = DeploymentService(database)
        versions, _ = await deployments.list_versions(
            target=ResearchTarget.OPERATIONAL_RISK
        )
        deployed = await deployments.deployed_version(ResearchTarget.OPERATIONAL_RISK)
        if deployed is not None:
            return True, "deployed"
        if not versions:
            return False, STAGE_NO_MODEL
        for version in versions:
            if await deployments.evaluation_for(version) is not None:
                return False, STAGE_NOT_DEPLOYED
        return False, STAGE_NOT_EVALUATED
    except Exception:  # noqa: BLE001 -- an unreadable registry is not an absence
        return None, "unknown"


@router.get("/", summary="Module 10 implementation status")
async def module_status(database: OptionalDbDependency) -> dict[str, Any]:
    """What this module does, and what it deliberately does not do.

    States the absent prediction endpoint in the body rather than leaving a
    researcher to discover it as a 404 and wonder whether they called it wrongly.

    ``trained`` and ``model_state`` are read from the deployment registry rather
    than written as a constant. They used to be a literal ``false``, which made
    the status say "not trained" in every state including the one where a model
    was trained, evaluated and deployed — a status endpoint that cannot be wrong
    is only useful while nothing can change, and this one can. The gate itself
    is unchanged: reading whether something is deployed is not predicting.
    """
    trained, model_state = await _model_state(database)
    return {
        "module": "risk",
        "number": 10,
        "implemented": True,
        "description": DESCRIPTION,
        "trained": trained,
        "model_state": model_state,
        # The sufficiency gate behind "no model has ever been trained here",
        # with real counts, so a client can state what is actually required
        # instead of a fixed threshold that would drift from the code that
        # enforces it.
        "training_gate": await training_gate_summary(database),
        "capabilities": {
            "risk_definitions": "implemented",
            "class_distribution": "implemented",
            # Implemented, and conditional: the endpoint answers only when a model
            # version is deployed. "not_implemented" would now be a false statement
            # about the code; reporting it as unconditional would be a false
            # statement about the service, since it refuses with nothing deployed.
            "risk_prediction": "implemented_gated_on_deployment",
        },
        "endpoints": {
            "write_a_rule": "POST /api/risk/definitions/",
            "list_rules": "GET /api/risk/definitions/",
            "read_a_rule": "GET /api/risk/definitions/{definition_id}",
            "read_a_rule_s_history": "GET /api/risk/definitions/name/{name}/versions",
            "measurable_variables": "GET /api/risk/variables",
            "class_distribution": (
                "POST /api/risk/definitions/{definition_id}/distribution"
            ),
            "predict": "POST /api/risk/predict",
        },
        "prediction_gate": {
            "rule": (
                "POST /api/risk/predict is answered only by a model version whose "
                "status is deployed. There is no default model, no fallback to the "
                "newest or best-scoring version, and no approximate answer."
            ),
            "with_nothing_deployed": (
                "409 with refusal=no_deployed_model. This is the expected response "
                "until a version is deployed, not a fault."
            ),
            "deployment_is_explicit": (
                "A model reaches deployed only through Module 17, by a request that "
                "names the version, the actor and the reason. Retraining writes a new "
                "version in experimental and does not touch the deployed one."
            ),
            "features_come_from_the_twin": (
                "The features are read from the pan's current digital twin state, "
                "column for column as the deployed model declared them. The request "
                "supplies no feature values, no window and no model id."
            ),
            "note": SERVICE_NOT_IMPLEMENTED,
        },
        "defaults": {
            "label_vocabulary": None,
            "note": (
                "This module supplies no severity classes and no thresholds. A "
                "definition arrives with its own class names, ordering and bounds, "
                "or no labels are produced at all."
            ),
        },
    }


@router.get(
    "/variables",
    response_model=RiskCatalogResponse,
    summary="The measurable variables a risk rule may reference",
)
def list_variables() -> RiskCatalogResponse:
    """Every quantity a rule can be written against, and where each comes from.

    Served from the same catalogue the evaluator and the write-time validation
    read, so what is published here is what will actually be enforced. Each entry
    names its source, the moment it was knowable, and how it is derived, because
    a rule that cites a variable is making a causal claim and a reader should be
    able to check it.
    """
    catalogue = RiskDefinitionService.catalog()
    return RiskCatalogResponse(
        variables=[RiskVariableResponse(**entry) for entry in catalogue["variables"]],
        retrospective_variables=catalogue["retrospective_variables"],
        severe_imbalance_ratio=catalogue["policy"]["severe_imbalance_ratio"],
        rare_class_minimum_rows=catalogue["policy"]["rare_class_minimum_rows"],
    )


@router.get(
    "/definitions",
    response_model=RiskDefinitionListResponse,
    summary="List stored risk definitions, newest first",
)
async def list_definitions(
    service: ServiceDependency,
    name: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    skip: Annotated[int, Query(ge=0)] = 0,
) -> RiskDefinitionListResponse:
    """Every rule written so far, optionally narrowed to one rule's name.

    Newest first, so a caller who wants the current state of a rule gets it
    without having to know how many versions it has accumulated.
    """
    definitions = await service.list_definitions(name=name, limit=limit, skip=skip)
    return RiskDefinitionListResponse(
        count=len(definitions),
        definitions=[RiskDefinitionRead.from_definition(item) for item in definitions],
    )


@router.post(
    "/definitions",
    response_model=RiskDefinitionCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Write a risk definition",
)
async def create_definition(
    draft: RiskDefinitionDraft,
    response: Response,
    service: ServiceDependency,
) -> RiskDefinitionCreateResponse:
    """Store a researcher's rule, at the next version of its name.

    Returns 201 when a new version was written and 200 when the identical rule
    already existed. The second case is not a failure and not a lesser success:
    posting an unchanged rule twice is one rule, and writing a second version
    would record a revision nobody made.

    The response carries the definition with the version number, the content hash
    and the superseded id, which together are what an experiment should cite. The
    hash is computed from the stored content, never accepted from the request, so a
    client cannot file a rule under an identity that does not describe it.

    Two things are refused here rather than stored. A criterion naming a variable
    outside the published catalogue is a 422, because a rule that depends on a
    quantity the project cannot measure cannot be checked. A criterion comparing
    in the wrong units, or ordering a categorical value, is also a 422, because
    both produce a class distribution that is a fact about the mistake rather than
    about any pan.
    """
    try:
        outcome = await service.create(draft)
    except RiskDefinitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if not outcome.created:
        response.status_code = status.HTTP_200_OK
    return RiskDefinitionCreateResponse(
        created=outcome.created,
        reason=outcome.reason,
        definition=RiskDefinitionRead.from_definition(outcome.definition),
    )


@router.get(
    "/definitions/name/{name}/versions",
    response_model=RiskVersionResponse,
    summary="One rule's whole version history, oldest first",
)
async def read_versions(name: str, service: ServiceDependency) -> RiskVersionResponse:
    """Every version of one rule, so a reader can see what was revised and when.

    Ordered forwards on purpose. A definition's history is an argument, and an
    argument reads in the order it was made.
    """
    versions = await service.list_versions(name)
    if not versions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No risk definition named {name!r} has been written.",
        )
    return RiskVersionResponse(
        name=name,
        count=len(versions),
        versions=[RiskDefinitionRead.from_definition(item) for item in versions],
    )


@router.get(
    "/definitions/{definition_id}",
    response_model=RiskDefinitionRead,
    summary="Read one stored risk definition",
)
async def read_definition(
    definition_id: str, service: ServiceDependency
) -> RiskDefinitionRead:
    """One definition, addressed by id or by specification hash.

    Both are accepted because a dataset cites the hash: reading a rule back the
    way a stored manifest names it is the check that the manifest still describes
    a rule that exists.
    """
    try:
        definition = await service.get(definition_id)
    except DocumentNotFoundError:
        try:
            definition = await service.get_by_hash(definition_id)
        except DocumentNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No risk definition matched {definition_id!r} as an id or as a "
                    "specification hash."
                ),
            ) from exc
    return RiskDefinitionRead.from_definition(definition)


@router.post(
    "/definitions/{definition_id}/distribution",
    response_model=RiskDistributionResponse,
    summary="Report a rule's class distribution over the stored records, before training",
)
async def read_distribution(
    definition_id: str,
    request: RiskDistributionRequest,
    risk: ServiceDependency,
    datasets: DatasetServiceDependency,
) -> RiskDistributionResponse:
    """Derive this rule's labels across the stored records and report their shape.

    This is the endpoint the module exists for. A class distribution is a joint
    property of a rule and a set of pans, visible in neither alone, and it is the
    last point at which a 97/2/1 split can be noticed — before an accuracy figure
    is quoted as though it meant something. The response says which classes were
    never observed, how lopsided the split is, and whether the distribution
    supports fitting a model at all.

    The rows are derived by the ordinary dataset builder with an operational-risk
    target pointing at this definition, so these are the same labels, with the
    same per-row provenance and the same causal audit, that a dataset built from
    this definition would carry. Asking for the distribution is not a shortcut
    around the audit.

    **Nothing is stored and nothing is trained.** The rows exist only for the
    length of the request, and ``trained_model`` in the response is always null so
    a reader does not have to wonder whether asking triggered a fit.
    """
    definition = await risk.get(definition_id)
    built, distribution = await datasets.risk_distribution(definition, request)
    return RiskDistributionResponse(
        definition=RiskDefinitionRead.from_definition(definition),
        distribution=distribution,
        record_count=len(built.rows),
        is_usable_for_training=distribution.is_usable_for_training,
        truncated_collections=list(built.truncated_collections),
        trained_model=None,
    )


@router.post(
    "/predict",
    summary="Predict operational risk with the deployed model",
    response_model=RiskPredictionResponse,
)
async def predict(
    body: RiskPredictionRequest,
    response: Response,
    service: PredictionServiceDependency,
) -> RiskPredictionResponse:
    """Score one pan at one anchor instant, with the model that is deployed.

    **Only a model version whose status is `deployed` answers this.** The gate is
    enforced in the service rather than here, so no caller can reach the estimator
    around it. With nothing deployed the request is refused with 409 and the reason
    is given; there is no fallback to the newest model, to the best-scoring one, or
    to any model at all. A refusal here is the correct behaviour, not a gap.

    The features are assembled from the pan's current digital twin state, and not
    from the request. A caller cannot supply feature values or a window, because
    inputs built any other way would produce a number that looks like a risk score
    and is not one. The row that reached the model is recorded with the prediction
    under the content hash ``input_snapshot_id``, so a stored risk figure can be
    checked against the state it claims to describe.

    The predicted class is returned with its probability, the model and version
    that produced it, the deployment it was answering under, the instant it was
    predicted, and the risk definition whose vocabulary the class comes from.
    Every one of those is in the response because a risk figure that cannot be
    traced to a model version, a rule and an input state cannot be defended later.
    """
    try:
        outcome = await service.predict(pan_id=body.pan_id, as_of=body.as_of)
    except PredictionRefusal as refusal:
        return JSONResponse(status_code=409, content=refusal.as_body())
    response.status_code = status.HTTP_201_CREATED
    return RiskPredictionResponse(**outcome)


@router.get(
    "/targets",
    summary="How this module relates to the dataset targets it can label",
)
def list_targets() -> dict[str, Any]:
    """The dataset targets available, and which one this module's rules feed.

    Included because the relationship is the non-obvious part: a rule here does
    not produce a risk score, it produces the *label column* of a Module 07
    dataset, and the two modules meet at ``operational_risk``.
    """
    return {
        "research_targets": [
            {
                "name": target.name.value,
                "kind": target.kind.value,
                "label_column": target.label_column,
                "derived_from_rule": target.requires_risk_definition,
                "description": target.description,
            }
            for target in TARGET_CATALOG
        ],
        "rule_backed_target": "operational_risk",
        "note": (
            "A risk definition supplies the label column and the thresholds for an "
            "operational_risk dataset. It supplies no features, and it is not a "
            "model: the dataset is built and reported from stored measurements, "
            "and whether anything is fitted to it afterwards is Module 08's "
            "decision, not this module's."
        ),
    }


__all__ = ["DESCRIPTION", "SERVICE_NOT_IMPLEMENTED", "router"]
