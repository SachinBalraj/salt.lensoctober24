"""HTTP payloads for the WhatsApp send and the farmer link.

**A recorded send carries no recommendation content.** Its only fields are
``recommendation_id`` and an optional ``operator_id``. There is no message field, no
recipient field, no phone field and no override of any kind, because every one of
those would be a way for a client to record a communication that the stored
recommendation does not support. The pan, the farmer, the message text and the
numbers in it are all read server-side from the recommendation and its pan — see
:mod:`app.services.whatsapp` and :mod:`app.services.whatsapp_delivery`.

**The link request is the one place text is supplied, and it records nothing.**
:class:`WhatsAppLinkRequest` carries the message an operator composed and read, so the
Module 12 panel can offer the current recommendation as a message without that text
being stored. It is deliberately separate from the send request: the send request's
freedom-from-content is what keeps an audit row tied to the stored recommendation,
and the link request's content is what lets a panel summarise live state the
recommendation document does not carry. The recipient is still not the caller's to
choose — it is the pan on the path — and no phone field exists.

**The response states what happened, and what did not.** ``action`` is always
``SEND_INITIATED``, ``delivery_status`` is always ``not_confirmed`` and
``delivery_confirmed`` is always ``false``. They are on the payload rather than
implied by its absence so a client cannot render "sent" by forgetting a check: the
field it would need is present, and it says the thing that is true.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DecisionAction
from app.models.recommendation_delivery import (
    DeliveryAction,
    DeliveryStatus,
    NotificationChannel,
)


class WhatsAppSendRequest(BaseModel):
    """The operator's wish to send one stored recommendation over WhatsApp."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    operator_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description=(
            "Who is sending, as supplied by the caller. Recorded on the audit "
            "entry. This backend has no authentication layer, so this is a "
            "self-reported identifier rather than an authenticated one — see the "
            "route docstring."
        ),
        examples=["op-2026-team-3"],
    )


class WhatsAppLinkRequest(BaseModel):
    """Operator-composed text for the farmer of one pan.

    Unlike :class:`WhatsAppSendRequest`, this body *does* carry a message. That is a
    deliberate difference and not an oversight: this endpoint builds a link, writes
    nothing and records nothing, and the message is a decision-support summary the
    operator has read on screen. The recipient is still not chosen here — it is the
    pan named on the path — so the request can decide *what* is said but not *to
    whom*, and no field exists for a phone number.

    ``message`` is required and non-blank: an empty message would open a chat with
    nothing to send, which is not a behaviour worth supporting.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    message: str = Field(
        min_length=1,
        max_length=4096,
        description=(
            "The exact text to pre-fill. Percent-encoded into the returned link, "
            "never stored."
        ),
        examples=["SaltLens DSS - Salt-pan recommendation\n\nRecommendation: HARVEST NOW"],
    )


class WhatsAppLinkRead(BaseModel):
    """A click-to-chat link for one pan's farmer, and the masked recipient.

    Writes nothing, records nothing and transmits nothing: it exists so the
    operator's browser can open WhatsApp with the text pre-filled. The number is
    present only inside the URL, exactly as the send response already returns it,
    and the separate masked field is what a panel can display.
    """

    pan_id: str
    recipient_contact_reference: str = Field(
        description="The farmer's number masked to its country code and last two digits.",
        examples=["+919•••••••10"],
    )
    click_to_chat_url: str = Field(
        description=(
            "WhatsApp's own click-to-chat URL with the message pre-filled. Opening "
            "it hands the message to WhatsApp on the operator's device; the send "
            "itself is performed there."
        ),
    )
    message_character_count: int = Field(ge=1)


class WhatsAppPreviewRead(BaseModel):
    """What would be sent, before anything is sent.

    Lets an operator read the exact text and check the masked recipient before
    committing to the press, so the message a farmer receives is one a person has
    seen. Reading this writes nothing.
    """

    recommendation_id: str
    pan_id: str
    recipient_contact_reference: str = Field(
        description=(
            "The farmer's number masked to its country code and last two digits. "
            "The unmasked number is never returned by any endpoint."
        ),
        examples=["+919•••••••10"],
    )
    message: str = Field(description="The exact text that would be pre-filled.")
    message_sha256: str
    message_template_version: str
    recommended_action: DecisionAction
    priority: str
    rule_name: str | None = None
    rule_version: int | None = None
    farmer_name: str | None = Field(
        default=None,
        description=(
            "The farmer as recorded on the pan. A convenience for the operator's "
            "confirmation, never a recipient: the number comes from the pan "
            "regardless of whether a name was given."
        ),
    )


class WhatsAppDeliveryRead(BaseModel):
    """One recorded send initiation.

    Every field here is a statement about a click. Nothing in this schema can be
    read as a delivery receipt, a farmer response or a completed action, and the
    three ``*_recorded`` booleans exist so that a client has no excuse for
    inferring otherwise from their absence.
    """

    model_config = ConfigDict(from_attributes=True)

    delivery_id: str
    recommendation_id: str
    pan_id: str
    recommended_action: DecisionAction
    priority: str
    rule_name: str | None = None
    rule_version: int | None = None
    farmer_name: str | None = Field(
        default=None,
        description=(
            "The farmer as recorded on the pan. A convenience for the operator's "
            "confirmation, never a recipient: the number comes from the pan "
            "regardless of whether a name was given."
        ),
    )
    specification_hash: str | None = None
    farmer_contact_reference: str = Field(
        description="The masked recipient. Never the unmasked number.",
    )
    channel: NotificationChannel
    action: DeliveryAction = Field(
        description=(
            "Always SEND_INITIATED. The record means the operator pressed the "
            "button, and nothing more."
        ),
    )
    delivery_status: DeliveryStatus = Field(
        description=(
            "Always not_confirmed. Click-to-chat leaves this system, so no "
            "delivery state is observable."
        ),
    )
    delivery_confirmed: bool = Field(
        description="Always false. No integration exists that could observe delivery."
    )
    farmer_response_recorded: bool = Field(
        description=(
            "Always false. A reply happens in WhatsApp; it becomes evidence only "
            "when a person records it."
        ),
    )
    physical_action_recorded: bool = Field(
        description=(
            "Always false. Physical actions are recorded against the pan, never by "
            "a send."
        ),
    )
    message_template_version: str
    message_sha256: str
    message_character_count: int
    operator_id: str | None = None
    initiated_at: datetime
    created_at: datetime

    @classmethod
    def from_delivery(cls, delivery: object) -> WhatsAppDeliveryRead:
        return cls.model_validate(delivery, from_attributes=True)


class WhatsAppSendRead(BaseModel):
    """The result of one press of the button.

    ``click_to_chat_url`` is what the operator's browser opens. It is returned
    rather than fetched: no request is made to WhatsApp from this server, no
    message is transmitted here, and no delivery is reported.
    """

    delivery: WhatsAppDeliveryRead = Field(
        description="The recorded send initiation, meaning SEND_INITIATED and nothing else.",
    )
    click_to_chat_url: str = Field(
        description=(
            "WhatsApp's own click-to-chat URL with the message pre-filled. Opening "
            "it hands the message to WhatsApp on the operator's device; the send "
            "itself is performed there."
        ),
    )
    message: str = Field(
        description="The exact text pre-filled into the WhatsApp chat.",
    )
    recipient_contact_reference: str = Field(
        description="The masked recipient, as recorded.",
    )
    delivery_confirmed: bool = Field(
        default=False,
        description=(
            "Always false. Present so a client renders what is true rather than "
            "what a send is assumed to imply."
        ),
    )

    @classmethod
    def from_send(cls, send: object) -> WhatsAppSendRead:
        return cls(
            delivery=WhatsAppDeliveryRead.from_delivery(send.delivery),  # type: ignore[attr-defined]
            click_to_chat_url=send.click_to_chat_url,  # type: ignore[attr-defined]
            message=send.message.text,  # type: ignore[attr-defined]
            recipient_contact_reference=send.recipient_contact_reference,  # type: ignore[attr-defined]
            delivery_confirmed=False,
        )


class WhatsAppRefusalResponse(BaseModel):
    """A send that could not be initiated, with a code the client branches on.

    Modelled rather than raised so the router can return it through the same
    ``JSONResponse`` path every other refusal in this application uses, keeping one
    error shape for clients to handle.

    ``code`` is the contract; ``detail`` is prose. The five codes mean different
    things to a user and need different next steps: record a contact on the pan,
    correct a number to E.164, or acknowledge the recommendation does not exist or
    named no action.
    """

    code: str = Field(
        description=(
            "One of: recommendation_unknown, recommendation_not_a_decision, "
            "pan_unknown, farmer_contact_not_configured, farmer_contact_invalid, "
            "demonstration_recommendation_not_sendable."
        ),
        examples=["farmer_contact_not_configured"],
    )
    detail: str = Field(
        description="What is missing, in a sentence an operator can act on."
    )
    recommendation_id: str | None = None


class WhatsAppDeliveryListResponse(BaseModel):
    """Every send initiated for one recommendation, newest first.

    More than one record is a normal outcome: re-sending is legitimate, and the
    collection is append-only so the trail shows how often the operator actually
    reached for the button.
    """

    recommendation_id: str
    found: bool = Field(
        description=(
            "False when no send has been initiated for this recommendation, which "
            "is a normal state rather than a failure."
        ),
    )
    deliveries: list[WhatsAppDeliveryRead]


__all__ = [
    "WhatsAppDeliveryListResponse",
    "WhatsAppDeliveryRead",
    "WhatsAppLinkRead",
    "WhatsAppLinkRequest",
    "WhatsAppPreviewRead",
    "WhatsAppRefusalResponse",
    "WhatsAppSendRead",
    "WhatsAppSendRequest",
]
