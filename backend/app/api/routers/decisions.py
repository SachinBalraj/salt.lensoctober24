"""Module 12 - the decision-support engine over HTTP.

Three route groups, and the separation between them is the module's argument
rather than a filing preference.

**The catalogue and the rules are how a decision rule is written.** They are
served from the same data the write path validates against and the evaluator
reads, so what is published is what is enforced: a criterion naming a variable
nobody defined is a 422, a unit that contradicts the catalogue is a 422, and a rule
whose ``uses_ml`` flag disagrees with its own criteria is a 422. A rule that could
be edited in place would make every recommendation already issued under it
unreconstructable, so writing one creates the next version and returns the existing
document when nothing has changed.

**``/advise`` combines the three kinds of knowledge and keeps them apart.** The
recorded twin state, the mass balance's answer and the model's stored estimate are
gathered and handed to the written rule; the rule alone chooses an action. The
response names each kind of evidence in its own field, and when no model estimate
was read it carries the exact label this project requires of that case.

**A refusal here is information, not an error to retry.** ``409`` means the
request was understood and no honest recommendation can be issued from it: no twin
state, a twin the mass balance cannot run, no rule written, or a rule that
declined to name an action. Each carries a code, so a client can distinguish
"record a state and ask again" from "this pan's evidence does not support any of
the four actions" without reading prose.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import get_db
from app.models.decision_rule import DecisionRuleDraft
from app.schemas.decisions import (
    DECISION_ACTIONS,
    DecisionAdviceRequest,
    DecisionAdviceResponse,
    DecisionCatalogResponse,
    DecisionLatestResponse,
    DecisionLogResponse,
    DecisionRefusalResponse,
    DecisionRuleCreateResponse,
    DecisionRuleListResponse,
    DecisionRuleRead,
    DecisionRuleVersionsResponse,
    DecisionVariableResponse,
)
from app.services.decision_reference_rule import reference_rule_draft
from app.services.decision_rules import DecisionRuleError, DecisionRuleService
from app.services.decisions import (
    MASS_BALANCE_MODEL_NAME,
    ML_UNAVAILABLE_LABEL,
    ML_UNAVAILABLE_REASONS,
    DecisionEngineService,
    DecisionRefusal,
)
from app.services.mass_balance import SIMULATION_ASSUMPTIONS, SIMULATOR_DISCLAIMER

router = APIRouter(prefix="/api/decisions", tags=["12-decisions"])

DESCRIPTION = (
    "Operational decision support: the current digital-twin state, a physics-based "
    "rainfall simulation, a stored ML risk estimate where one exists, and a versioned "
    "written rule combined into one of four actions — harvest now, transfer brine, "
    "protect/drain, or wait and recheck. Prediction, simulation and recommendation are "
    "reported separately and never merged into one another's number."
)

ENDPOINTS = [
    "GET  /api/decisions/",
    "GET  /api/decisions/variables",
    "GET  /api/decisions/rules",
    "POST /api/decisions/rules",
    "GET  /api/decisions/rules/{rule_id}",
    "GET  /api/decisions/rules/name/{name}/versions",
    "POST /api/decisions/advise",
    "GET  /api/decisions/{pan_id}/latest",
    "GET  /api/decisions/log",
]

#: Stated as a list of things this module will not do, because that is the useful
#: question to ask of a module that emits operational instructions.
WHAT_THIS_MODULE_DOES_NOT_DO = (
    "The action comes from a written rule, or there is no response. It will not pick "
    "a threshold for an operator, will not carry a fallback action of its own, will "
    "not score the model to obtain an estimate (it reads a stored prediction, so a "
    "recommendation cannot disagree with Module 10 about what the model would say), "
    "and will not present a rule's agreement with an action as a probability. A "
    "recommendation without a model estimate is issued and labelled as such; one "
    "whose simulation cannot be computed is refused rather than filled in."
)


def get_decision_service() -> DecisionEngineService:
    """The engine, overridable in tests."""
    return DecisionEngineService(get_db())


def get_decision_rule_service() -> DecisionRuleService:
    """The rule store, overridable in tests."""
    return DecisionRuleService(get_db())


ServiceDependency = Annotated[DecisionEngineService, Depends(get_decision_service)]
RuleDependency = Annotated[DecisionRuleService, Depends(get_decision_rule_service)]


def _refusal_response(exc: DecisionRefusal) -> JSONResponse:
    """A refusal as a 409 carrying its code.

    409 rather than 404 or 422: the request was well formed and the pan exists,
    and what cannot be produced is a recommendation from the evidence on hand.
    That is a different outcome from "no such pan" and a different one from "your
    body was wrong", and a client should be able to tell them apart.
    """
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=DecisionRefusalResponse(**exc.as_body()).model_dump(mode="json"),
    )


@router.get("/", summary="Module 12 implementation status")
def module_status() -> dict[str, Any]:
    """Report that Module 12 is implemented, and state the action vocabulary.

    This endpoint touches nothing and cannot fail, so a client that only polls
    status still learns the two things that matter before wiring a control: the
    actions are a closed set of four, and a recommendation is assembled from
    evidence rather than from the module's own judgement.
    """
    return {
        "module": "decision-support",
        "module_number": 12,
        "status": "implemented",
        "description": DESCRIPTION,
        "endpoints": ENDPOINTS,
        "actions": list(DECISION_ACTIONS),
        "action_vocabulary_is_closed": True,
        "combines": {
            "digital_twin": "Module 05 — the current recorded state",
            "simulation": (
                f"Module 06 — {MASS_BALANCE_MODEL_NAME}, deterministic mass balance, "
                "is_ml=false"
            ),
            "ml_prediction": (
                "Module 10 — the newest stored operational-risk prediction, read "
                "rather than recomputed, and null when none is stored"
            ),
            "shap_explanation": (
                "Module 11 — attached by id when the prediction has been explained, "
                "and never fabricated"
            ),
            "rules": (
                "Module 12 — a versioned, written rule is the only thing that "
                "chooses the action"
            ),
        },
        "ml_unavailable_label": ML_UNAVAILABLE_LABEL,
        "ml_unavailable_reasons": dict(ML_UNAVAILABLE_REASONS),
        "simulation": {
            "model_name": MASS_BALANCE_MODEL_NAME,
            "is_ml": False,
            "assumptions": list(SIMULATION_ASSUMPTIONS),
            "disclaimer": SIMULATOR_DISCLAIMER,
        },
        "does_not_do": WHAT_THIS_MODULE_DOES_NOT_DO,
    }


@router.get(
    "/variables",
    response_model=DecisionCatalogResponse,
    summary="The quantities a decision rule may reference",
)
def list_variables() -> DecisionCatalogResponse:
    """Every quantity a rule can be written against, and which part of the system it comes from.

    Served from the same catalogue the write path validates against and the
    evaluator reads, so what is published here is what will actually be enforced.
    Each entry names its source, because a rule that cites a variable is making a
    claim about what kind of evidence produced it, and a reader should be able to
    check that without reading the engine.
    """
    catalogue = DecisionRuleService.catalog()
    return DecisionCatalogResponse(
        variables=[DecisionVariableResponse(**entry) for entry in catalogue["variables"]],
        actions=catalogue["actions"],
        policy=catalogue["policy"],
    )


@router.get(
    "/rules/reference-draft",
    response_model=DecisionRuleDraft,
    summary="The reference rule, as a draft ready to post",
)
async def get_reference_draft() -> DecisionRuleDraft:
    """A complete, writable rule that contains no threshold of its own.

    Served so an operator can read exactly what this project proposes to decide
    with, and post it unchanged or edit it first. It is offered as a **draft**
    rather than written into storage on startup, for two reasons. A default rule
    that appeared by itself could not be revised without every recommendation
    already issued silently changing its basis; and a rule this project supplies
    should be an act the operator takes and is recorded as theirs.

    Every criterion names its bound with ``bound_variable``, so the comparisons are
    made against limits the operator configures per pan. A pan with one of them
    unconfigured is reported as unevaluable instead of being advised against a
    number that came from here.
    """
    return reference_rule_draft()


@router.get(
    "/rules",
    response_model=DecisionRuleListResponse,
    summary="List stored decision rules, newest first",
)
async def list_rules(
    service: RuleDependency,
    name: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    skip: Annotated[int, Query(ge=0)] = 0,
) -> DecisionRuleListResponse:
    """Every rule written so far, optionally narrowed to one rule's name."""
    rules = await service.list_rules(name=name, limit=limit, skip=skip)
    return DecisionRuleListResponse(
        count=len(rules),
        rules=[DecisionRuleRead.from_rule(rule) for rule in rules],
    )


@router.post(
    "/rules",
    response_model=DecisionRuleCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Write a decision rule",
)
async def create_rule(
    draft: DecisionRuleDraft,
    response: Response,
    service: RuleDependency,
) -> DecisionRuleCreateResponse:
    """Store an author's rule, at the next version of its name.

    Returns 201 when a new version was written and 200 when the identical rule
    already existed. The second case is not a failure: posting an unchanged rule
    twice is one rule, and writing a second version would record a revision nobody
    made and leave every recommendation citing a rule that never changed.

    Three things are refused with a 422 rather than stored, because each is a
    mistake in the rule rather than in the data: a criterion naming a variable
    outside the published catalogue, a criterion whose declared unit contradicts
    the catalogue's, and a ``uses_ml`` flag that disagrees with whether the
    criteria actually read a model's estimate.
    """
    try:
        outcome = await service.create(draft)
    except (DecisionRuleError, KeyError, LookupError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if not outcome.created:
        response.status_code = status.HTTP_200_OK
    return DecisionRuleCreateResponse.from_outcome(outcome)


@router.get(
    "/rules/{rule_id}",
    response_model=DecisionRuleRead,
    summary="Read one decision rule version",
)
async def read_rule(rule_id: str, service: RuleDependency) -> DecisionRuleRead:
    """One stored rule version, exactly as written."""
    try:
        rule = await service.get(rule_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No decision rule {rule_id!r} is stored.",
        ) from exc
    return DecisionRuleRead.from_rule(rule)


@router.get(
    "/rules/name/{name}/versions",
    response_model=DecisionRuleVersionsResponse,
    summary="Every version of one rule, oldest first",
)
async def read_versions(name: str, service: RuleDependency) -> DecisionRuleVersionsResponse:
    """One rule's history, in the order it was written.

    Ordered forwards so the sequence reads as a revision trail, which is the only
    direction in which "the threshold was raised in version 3" is a statement
    someone can check.
    """
    try:
        rules = await service.list_versions(name)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No decision rule named {name!r} has been written.",
        ) from exc
    return DecisionRuleVersionsResponse(
        name=name,
        count=len(rules),
        rules=[DecisionRuleRead.from_rule(rule) for rule in rules],
    )


@router.post(
    "/advise",
    response_model=DecisionAdviceResponse,
    summary="Issue one recommendation for a pan",
)
async def advise(
    payload: DecisionAdviceRequest,
    service: ServiceDependency,
) -> DecisionAdviceResponse:
    """Combine twin state, physics, a stored ML estimate and a written rule into one action.

    The request body names a pan and nothing else. It cannot supply features, a
    model, a threshold or an action, because a recommendation whose inputs the
    caller also chose is a recommendation about the caller's assumptions rather
    than about the pan.

    The response always carries seven things: the action, the reason, the inputs
    with where each came from, the simulation result, the model result when one was
    read, the rule version, and the timestamp. When no model estimate was read,
    ``ml_result`` is null, ``basis`` is ``physics_and_rules`` and ``notice``
    contains this project's exact label for that case.

    Refused with a 409 and a code when the recommendation cannot be issued
    honestly: ``no_twin_state`` when the pan has no recorded state,
    ``simulation_inputs_missing`` when the mass balance cannot run over the twin's
    own readings, ``no_rule_defined`` when no rule has been written, and
    ``rule_undecided`` when the rule read the pan and still named no action.
    """
    try:
        stored = await service.advise(
            payload.pan_id,
            as_of=payload.as_of,
            rainfall_mm=payload.rainfall_mm,
            rule_id=payload.rule_id,
        )
    except DecisionRefusal as exc:
        return _refusal_response(exc)  # type: ignore[return-value]
    return DecisionAdviceResponse.from_recommendation(stored)


@router.get(
    "/{pan_id}/latest",
    response_model=DecisionLatestResponse,
    summary="The newest recommendation for a pan",
)
async def latest_advice(pan_id: str, service: ServiceDependency) -> DecisionLatestResponse:
    """The most recent recommendation issued for this pan, if there is one.

    Reports ``found: false`` rather than 404 for a pan with no recommendation on
    record: that is a normal state for a pan nobody has asked about yet, and a
    client needs to tell it apart from a failure.
    """
    stored = await service.latest(pan_id)
    if stored is None:
        return DecisionLatestResponse(found=False, advice=None)
    return DecisionLatestResponse(
        found=True, advice=DecisionAdviceResponse.from_recommendation(stored)
    )


@router.get(
    "/log",
    response_model=DecisionLogResponse,
    summary="The advice log, newest first",
)
async def advice_log(
    service: ServiceDependency,
    pan_id: str | None = None,
    action: str | None = Query(default=None, description="One of the four actions."),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    skip: Annotated[int, Query(ge=0)] = 0,
) -> DecisionLogResponse:
    """Every recommendation issued, optionally narrowed to a pan or to one action.

    Ordered by issue time rather than creation time, because a recommendation's
    operational moment is when it was issued and the two differ when a request is
    replayed.

    An unrecognised ``action`` is a 422 rather than an empty list: a caller who
    typed ``HARVEST`` instead of ``HARVEST NOW`` should be told, not shown a
    confidently empty log.
    """
    if action is not None and action not in DECISION_ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"{action!r} is not one of the four actions this project recommends. "
                f"Choose one of: {', '.join(DECISION_ACTIONS)}."
            ),
        )
    rows = await service.log(pan_id=pan_id, action=action, limit=limit, skip=skip)
    return DecisionLogResponse(
        count=len(rows), advice=[DecisionAdviceResponse.from_recommendation(row) for row in rows]
    )


__all__ = ["DESCRIPTION", "ENDPOINTS", "WHAT_THIS_MODULE_DOES_NOT_DO", "router"]
