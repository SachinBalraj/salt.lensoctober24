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
from app.schemas.whatsapp import (
    WhatsAppDeliveryListResponse,
    WhatsAppDeliveryRead,
    WhatsAppPreviewRead,
    WhatsAppRefusalResponse,
    WhatsAppSendRead,
    WhatsAppSendRequest,
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
from app.services.whatsapp_delivery import WhatsAppRefusal, WhatsAppService

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
    "GET  /api/decisions/{recommendation_id}/whatsapp-preview",
    "POST /api/decisions/{recommendation_id}/whatsapp-send",
    "GET  /api/decisions/{recommendation_id}/whatsapp-sends",
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


def get_whatsapp_service() -> WhatsAppService:
    """The WhatsApp service, overridable in tests.

    Built from ``get_db()`` at request time rather than at import, so a request for
    an unreachable database produces the same 503 every other route here produces
    and a malformed body is still a 422.
    """
    return WhatsAppService(get_db())


WhatsAppDependency = Annotated[WhatsAppService, Depends(get_whatsapp_service)]


def _whatsapp_refusal(exc: WhatsAppRefusal) -> JSONResponse:
    """A refusal as a 409 carrying its code.

    409 for the same reason Module 12's refusals are: the request was well formed
    and the recommendation exists, and what cannot be produced is a send from the
    evidence on hand. A missing contact is not a 404, and a bad phone number is
    not a 422 — both are states of the data, and a client needs to tell them apart
    from a malformed request and from a missing record.
    """
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=WhatsAppRefusalResponse(**exc.as_body()).model_dump(mode="json"),
    )


@router.get(
    "/{recommendation_id}/whatsapp-preview",
    response_model=WhatsAppPreviewRead,
    summary="The WhatsApp message that would be sent, and to whom",
)
async def whatsapp_preview(
    recommendation_id: str,
    service: WhatsAppDependency,
) -> WhatsAppPreviewRead:
    """The exact text and masked recipient for one recommendation. Writes nothing.

    Exists so the operator can read the message before a person receives it. It
    shares one code path with the send itself, so the preview cannot show text the
    send would then produce differently — a preview assembled separately would let
    an operator approve a message that is not the one sent.

    The recipient is masked. The unmasked number exists on the pan document, where
    the send needs it, and is not returned by any endpoint: a response body is the
    easiest place in this system for a number to end up in a browser history, a
    proxy log or a screenshot.
    """
    try:
        preview = await service.preview(recommendation_id)
        recommendation = preview.recommendation
    except WhatsAppRefusal as exc:
        return _whatsapp_refusal(exc)  # type: ignore[return-value]

    from app.models.phone import mask_phone

    reference = mask_phone(preview.recipient_number)
    if reference is None:  # pragma: no cover - preview resolved a usable number
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The configured number could not be masked for display.",
        )
    return WhatsAppPreviewRead(
        recommendation_id=recommendation.recommendation_id,
        pan_id=recommendation.pan_id,
        recipient_contact_reference=reference,
        message=preview.message.text,
        message_sha256=preview.message.sha256,
        message_template_version=preview.message.template_version,
        recommended_action=recommendation.action,  # type: ignore[arg-type]
        priority=recommendation.priority.value,
        rule_name=recommendation.rule_name,
        rule_version=recommendation.rule_version,
        farmer_name=preview.farmer_name,
    )


@router.post(
    "/{recommendation_id}/whatsapp-send",
    response_model=WhatsAppSendRead,
    summary="Initiate a WhatsApp send of one stored recommendation",
)
async def whatsapp_send(
    recommendation_id: str,
    payload: WhatsAppSendRequest,
    service: WhatsAppDependency,
) -> WhatsAppSendRead:
    """Record that an operator initiated a WhatsApp send, and return the link.

    **Nothing is transmitted from this server.** The response carries WhatsApp's
    ``wa.me`` click-to-chat URL; the operator's browser opens it, WhatsApp opens on
    the operator's own device, and the operator presses send there. There is no
    WhatsApp Business API account, token or webhook behind this, and the response
    says ``action: SEND_INITIATED`` and ``delivery_status: not_confirmed`` because
    those are the only two facts available — the system is not on the conversation
    and receives no receipt.

    **The body cannot influence the message.** It carries ``operator_id`` and
    nothing else; there is no message, recipient or number field to send, because
    any of them would be a way to record a communication the stored recommendation
    does not support. The pan is derived from the recommendation, so the farmer
    contacted cannot be chosen by the caller.

    **Authentication.** This backend has none — no route in the application
    resolves a user, a token or a role — so this endpoint is guarded exactly as
    every other route is, and ``operator_id`` is a self-reported string stored as
    such. That is a known gap, recorded rather than papered over: adding auth to
    this one route would leave the other twenty endpoints unguarded and make the
    application harder to reason about than it is today.

    **What writing the record does not do.** It does not modify the recommendation,
    does not create an operator decision, and does not touch ``verification_state``,
    independent verification, a physical-action record or any field-validation
    state. A send is a communication event and stays in its own collection.
    """
    try:
        send = await service.initiate_send(
            recommendation_id, operator_id=payload.operator_id
        )
    except WhatsAppRefusal as exc:
        return _whatsapp_refusal(exc)  # type: ignore[return-value]
    return WhatsAppSendRead.from_send(send)


@router.get(
    "/{recommendation_id}/whatsapp-sends",
    response_model=WhatsAppDeliveryListResponse,
    summary="Every WhatsApp send initiated for one recommendation",
)
async def whatsapp_send_history(
    recommendation_id: str,
    service: WhatsAppDependency,
) -> WhatsAppDeliveryListResponse:
    """The sends recorded for one recommendation, newest first.

    More than one row is normal: an operator may re-send, and the collection is
    append-only precisely so that repeated presses are visible as presses. Every row
    says ``SEND_INITIATED``; none of them says anything was delivered.
    """
    rows = await service.list_for_recommendation(recommendation_id)
    return WhatsAppDeliveryListResponse(
        recommendation_id=recommendation_id,
        found=bool(rows),
        deliveries=[WhatsAppDeliveryRead.from_delivery(row) for row in rows],
    )


__all__ = ["DESCRIPTION", "ENDPOINTS", "WHAT_THIS_MODULE_DOES_NOT_DO", "router"]
