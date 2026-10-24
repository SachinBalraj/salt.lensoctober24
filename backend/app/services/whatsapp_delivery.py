"""Initiating a WhatsApp send for a stored recommendation.

**The order of operations is the security property.** The recommendation is loaded
first and the pan is derived from it, so the pan whose farmer is contacted cannot
be chosen by the caller — there is no pan identifier in the request to choose one
with, which is what closes the obvious cross-pan hole. The contact is read from that
pan and normalised. Only then is the message built and only then is anything
written. A refusal at any step writes nothing, so a failed attempt leaves no trace
in the audit collection that could later be mistaken for a send.

**What a refusal means.** :class:`WhatsAppRefusal` codes are states of the system
rather than mistakes in the request: no such recommendation, a recommendation that
named no action, a pan with no farmer contact recorded, a stored number that no
longer normalises. Each is a 409 with a code a client branches on, matching how
Module 12 and Module 14 report their own refusals, so one client convention covers
the three.

**Authentication: the same gap as everywhere else in this backend.** Every route in
this application resolves a database and nothing else — there is no user, no token
check and no role check anywhere in the router layer — so this endpoint inherits
that rather than inventing an auth mechanism nothing else uses. :attr:`operator_id`
is therefore a caller-supplied string, exactly as ``FeedbackCreate.operator_id`` is,
and it is stored as "the operator who said they were this operator". That is a real
limitation and it is stated in the route docstring and in the docs rather than papered
over with a dependency that would guard this one route and leave the other twenty
unguarded.

**What writing a record does and does not mean.** It records that the operator
pressed the button. The record cannot assert delivery, a farmer response or a
physical action — see :mod:`app.models.recommendation_delivery`, whose validators
refuse those claims — and nothing in this service touches a recommendation, an
operator decision, an outcome or any field-validation state. A send is a
communication event and stays in its own collection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.db.collections import RECOMMENDATION_DELIVERIES
from app.db.errors import DocumentNotFoundError
from app.db.repository import MongoRepository
from app.models.phone import InvalidWhatsAppNumber, normalize_phone
from app.models.recommendation import Recommendation
from app.models.recommendation_delivery import (
    DeliveryAction,
    DeliveryStatus,
    NotificationChannel,
    RecommendationDelivery,
)
from app.models.salt_pan import SaltPan
from app.services.salt_pans import SaltPanService
from app.services.whatsapp import (
    WhatsAppMessage,
    build_click_to_chat_url,
    build_link_url,
    build_message,
    contact_reference,
)


class WhatsAppRefusal(RuntimeError):
    """A send that cannot be initiated, with a code a client can branch on.

    Raised rather than returned, so the route can convert it to a 409 carrying the
    code without this service importing FastAPI — the same split
    :class:`~app.services.feedback.FeedbackRefusal` uses.
    """

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        recommendation_id: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.recommendation_id = recommendation_id

    def as_body(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "recommendation_id": self.recommendation_id,
        }


@dataclass(frozen=True)
class WhatsAppSend:
    """What one press of the button produced: the record, and the link to follow.

    The URL and the record travel together because they describe the same act. A
    response carrying a URL built from a different render than the one hashed into
    the audit row would mean the operator sent something other than what was
    recorded, which is the failure this pairing exists to make impossible.
    """

    delivery: RecommendationDelivery
    click_to_chat_url: str
    message: WhatsAppMessage
    recipient_contact_reference: str

    @property
    def delivery_confirmed(self) -> bool:
        """Always ``False``. Named so a client cannot render "confirmed" from it."""
        return False


@dataclass(frozen=True)
class WhatsAppPreview:
    """What a send of this recommendation would be, resolved but not recorded.

    Returned as one object rather than a tuple because the read endpoint needs the
    farmer's name as well as the message and the number, and a four-element tuple
    invites unpacking two of the three in the wrong order — a mistake that would
    show a message built for one pan beside the masked number of another.
    """

    recommendation: Recommendation
    message: WhatsAppMessage
    recipient_number: str
    farmer_name: str | None


@dataclass(frozen=True)
class WhatsAppLink:
    """A click-to-chat link for text this client composed, and the masked recipient.

    Distinct from :class:`WhatsAppSend`: nothing is stored, nothing is hashed into an
    audit row, and the text did not come from a recommendation. It exists so the
    operator can open a conversation with the number already on file, with a message
    a person has read on screen, without the number ever being returned as a value
    the browser can hold onto.
    """

    click_to_chat_url: str
    recipient_contact_reference: str
    message_character_count: int


def contact_number_for_pan(
    pan: SaltPan, *, recommendation_id: str | None = None
) -> str:
    """The farmer's number, normalised, or a refusal saying why not.

    Missing and invalid are separate codes because they are separate problems: one
    means nobody has recorded a contact for this pan, the other means one was
    recorded in a form that cannot be dialled. A client shows a different message and
    a different next step for each. Module-level so both the stored-recommendation
    send and the operator-composed link resolve a contact the same way.
    """
    if pan.farmer_whatsapp is None:
        raise WhatsAppRefusal(
            "farmer_contact_not_configured",
            (
                f"No farmer WhatsApp number is configured for pan "
                f"{pan.pan_id!r}. Record one on the pan before sending."
            ),
            recommendation_id=recommendation_id,
        )
    try:
        number = normalize_phone(pan.farmer_whatsapp)
    except InvalidWhatsAppNumber as exc:
        raise WhatsAppRefusal(
            "farmer_contact_invalid",
            (
                f"The WhatsApp number configured for pan {pan.pan_id!r} is not "
                f"usable: {exc.reason}."
            ),
            recommendation_id=recommendation_id,
        ) from None
    if number is None:
        # Unreachable given the check above, but a guard rather than an
        # assumption: the URL builder refuses a null number, and finding that
        # out as a ValueError from deep inside string formatting would be a
        # worse error than a code.
        raise WhatsAppRefusal(
            "farmer_contact_not_configured",
            f"No farmer WhatsApp number is configured for pan {pan.pan_id!r}.",
            recommendation_id=recommendation_id,
        )
    return number


class WhatsAppService:
    """Build and record operator-initiated WhatsApp sends."""

    def __init__(self, database: Any) -> None:
        self._database = database
        self._resolved_repository: MongoRepository[RecommendationDelivery] | None = None

    @property
    def _repository(self) -> MongoRepository[RecommendationDelivery]:
        if self._resolved_repository is None:
            self._resolved_repository = MongoRepository(
                self._database, RECOMMENDATION_DELIVERIES, RecommendationDelivery
            )
        return self._resolved_repository

    # -- reads ---------------------------------------------------------------

    async def _recommendation(self, recommendation_id: str) -> Recommendation:
        """The stored recommendation, or a refusal.

        Refuses rather than returning ``None`` because every path through this
        service needs a real decision record; there is no partial operation to
        offer a caller when the recommendation is absent.
        """
        from app.services.decisions import DecisionEngineService

        try:
            recommendation = await DecisionEngineService(
                self._database
            ).get(recommendation_id)
        except DocumentNotFoundError:
            raise WhatsAppRefusal(
                "recommendation_unknown",
                (
                    f"No recommendation {recommendation_id!r} exists on the server, "
                    "so there is nothing to send."
                ),
                recommendation_id=recommendation_id,
            ) from None
        if not recommendation.is_decision:
            raise WhatsAppRefusal(
                "recommendation_not_a_decision",
                (
                    f"Recommendation {recommendation_id!r} named no action, so there "
                    "is no recommendation to communicate."
                ),
                recommendation_id=recommendation_id,
            )
        if recommendation.twin is not None and recommendation.twin.is_demonstration:
            raise WhatsAppRefusal(
                "demonstration_recommendation_not_sendable",
                (
                    f"Recommendation {recommendation_id!r} was built from "
                    "demonstration data. A demonstration recommendation is never "
                    "sent to a farmer."
                ),
                recommendation_id=recommendation_id,
            )
        return recommendation

    async def _pan(self, pan_id: str, *, recommendation_id: str) -> SaltPan:
        """The pan the recommendation was made for.

        Reached only as ``recommendation.pan_id``, which is the point: the caller
        cannot name a different pan and have that pan's farmer contacted about
        another pan's recommendation.
        """
        try:
            return await SaltPanService(self._database).get_pan(pan_id)
        except DocumentNotFoundError:
            raise WhatsAppRefusal(
                "pan_unknown",
                (
                    f"Pan {pan_id!r} is not on the registry, so no farmer contact "
                    "can be read for it."
                ),
                recommendation_id=recommendation_id,
            ) from None

    async def _contact(self, pan: SaltPan, *, recommendation_id: str) -> str:
        """The farmer's number, normalised, or a refusal carrying the recommendation.

        Delegates to :func:`contact_number_for_pan` so the send and the
        operator-composed link refuse a missing or unusable number in exactly the
        same terms; the recommendation id is attached here for the send endpoints.
        """
        return contact_number_for_pan(pan, recommendation_id=recommendation_id)

    async def preview(self, recommendation_id: str) -> WhatsAppPreview:
        """Resolve what *would* be sent, without writing anything.

        Used by the read endpoint so the operator can see the exact text before
        sending it to a person. Sharing one code path with
        :meth:`initiate_send` is deliberate: a preview computed separately could
        show text the send then does not produce, and the operator would have
        approved a message nobody sent.
        """
        recommendation = await self._recommendation(recommendation_id)
        pan = await self._pan(
            recommendation.pan_id, recommendation_id=recommendation.recommendation_id
        )
        number = await self._contact(
            pan, recommendation_id=recommendation.recommendation_id
        )
        return WhatsAppPreview(
            recommendation=recommendation,
            message=build_message(recommendation, pan),
            recipient_number=number,
            farmer_name=pan.farmer_name,
        )

    async def build_farmer_link(self, pan_id: str, message_text: str) -> WhatsAppLink:
        """A ``wa.me`` link for operator-composed text addressed to one pan's farmer.

        Writes nothing. The number is read from the stored pan and never returned as
        a value: the caller receives a URL, which is what the browser needs and is no
        more than the run-of-the-mill send endpoint already hands back. The pan whose
        farmer is reached is chosen by the identifier on the path, so this is a link
        builder rather than a way to message an arbitrary number — there is no field
        for a number, only a pan that must already be on the registry.

        A pan that is not registered raises the repository's own
        ``DocumentNotFoundError``, which the application renders as a 404, and a
        missing or unusable stored number raises :class:`WhatsAppRefusal` with the
        same codes the send uses.
        """
        pan = await SaltPanService(self._database).get_pan(pan_id)
        number = contact_number_for_pan(pan)
        reference = contact_reference(pan)
        if reference is None:  # pragma: no cover - contact_number_for_pan resolved one
            raise WhatsAppRefusal(
                "farmer_contact_not_configured",
                f"No farmer WhatsApp number is configured for pan {pan.pan_id!r}.",
            )
        return WhatsAppLink(
            click_to_chat_url=build_link_url(number, message_text),
            recipient_contact_reference=reference,
            message_character_count=len(message_text),
        )

    # -- writes --------------------------------------------------------------

    async def initiate_send(
        self,
        recommendation_id: str,
        *,
        operator_id: str | None = None,
    ) -> WhatsAppSend:
        """Record that the operator initiated a WhatsApp send, and hand back the URL.

        Writes exactly one document, and it is a communication record: the
        recommendation is read and never modified, no operator decision is created,
        and no verification, outcome or field-validation state is touched. There is
        deliberately no second write anywhere in this method — the record of a send
        is the record of a send.

        The URL is returned rather than opened. The service cannot open anything;
        the caller's browser follows the link, which is the entire mechanism.
        Building it here, from the same message that was hashed into the record, is
        what makes the URL and the audit entry describe the same text.
        """
        recommendation = await self._recommendation(recommendation_id)
        pan = await self._pan(
            recommendation.pan_id, recommendation_id=recommendation.recommendation_id
        )
        number = await self._contact(
            pan, recommendation_id=recommendation.recommendation_id
        )
        message = build_message(recommendation, pan)

        # Built before the write so an unusable number cannot leave a record
        # behind. `_contact` has already refused the cases that would fail here, so
        # this is a second line of defence rather than the primary check.
        url = build_click_to_chat_url(number, message)

        reference = contact_reference(pan)
        if reference is None:
            raise WhatsAppRefusal(
                "farmer_contact_not_configured",
                f"No farmer WhatsApp number is configured for pan {pan.pan_id!r}.",
                recommendation_id=recommendation.recommendation_id,
            )

        assert recommendation.action is not None  # guaranteed by _recommendation

        delivery = RecommendationDelivery(
            recommendation_id=recommendation.recommendation_id,
            pan_id=recommendation.pan_id,
            recommended_action=recommendation.action,
            priority=recommendation.priority.value,
            rule_name=recommendation.rule_name,
            rule_version=recommendation.rule_version,
            specification_hash=recommendation.specification_hash,
            farmer_contact_reference=reference,
            farmer_name=pan.farmer_name,
            channel=NotificationChannel.WHATSAPP,
            action=DeliveryAction.SEND_INITIATED,
            delivery_status=DeliveryStatus.NOT_CONFIRMED,
            message_template_version=message.template_version,
            message_sha256=message.sha256,
            message_character_count=message.character_count,
            operator_id=operator_id,
        )
        stored = await self._repository.insert(delivery)
        return WhatsAppSend(
            delivery=stored,
            click_to_chat_url=url,
            message=message,
            recipient_contact_reference=reference,
        )

    async def list_for_recommendation(
        self, recommendation_id: str, *, limit: int = 20
    ) -> list[RecommendationDelivery]:
        """Every send initiated for one recommendation, newest first.

        Multiple records are the normal case rather than an anomaly: re-sending is
        legitimate, and an index that permitted only one would push an operator to
        reuse the first audit row for a second press, which is precisely the
        misreporting this collection exists to prevent.
        """
        return await self._repository.find(
            {"recommendation_id": recommendation_id},
            sort=[("initiated_at", -1)],
            limit=limit,
        )

    async def count(self, **query: Any) -> int:
        return await self._repository.count(query)


__all__ = [
    "WhatsAppLink",
    "WhatsAppRefusal",
    "WhatsAppService",
    "contact_number_for_pan",
]
