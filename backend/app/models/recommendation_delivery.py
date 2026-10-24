"""The record that an operator opened WhatsApp for a recommendation.

One document per press of the button. Append-only, like
:class:`~app.models.recommendation.Recommendation` and
:class:`~app.models.operator_decision.OperatorDecision`, because a communication
record that can be edited is not a record of a communication.

**What this document does and does not claim.** It records that an operator
initiated a WhatsApp action. That is the whole of it. It does not record delivery,
because click-to-chat hands the message to WhatsApp in the operator's own browser
and the system never speaks to Meta afterwards — there is no webhook to receive and
no receipt to wait for. It does not record the farmer receiving anything, agreeing
to anything, or acting on anything. Those are separate events with their own
records, and a send is not one of them.

**The negatives are fields, not omissions.** A reader who finds this document in
six months finds ``delivery_confirmed``, ``farmer_response_recorded`` and
``physical_action_recorded`` stated on the record as ``False``. They are ``False``
on every document this model can produce, and the validator refuses a document
claiming otherwise — so the honest reading is the only reading, and it cannot be
lost by someone reading only the fields that happen to be present.

**Why it is its own collection and not a field on the operator decision.**
``operator_decisions`` carries a unique index of one decision per recommendation
and holds a terminal disposition. A send may legitimately happen several times for
one recommendation — re-sending after a corrected number, or re-sending because the
first was never opened — and it precedes rather than disposes of any decision.
Folding it in would force it to be either singular or terminal, and it is neither.

**The message is not stored.** :attr:`RecommendationDelivery.message_sha256` and
:attr:`~RecommendationDelivery.message_character_count` are enough to prove *which*
text was sent and to detect a later change to the builder, and the recommendation it
was derived from is stored in full. The text itself carries the farmer's name, and
there is nothing in this project's privacy model that requires a second copy of it.

**The contact is masked.** :attr:`farmer_contact_reference` keeps the country code
and the last two digits. The unmasked number lives on the pan document, where it is
needed; a copy in a collection whose purpose is to be listed in an audit trail is
not needed, and is the copy most likely to end up in a log.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.models.base import (
    BaseDocument,
    UtcDateTime,
    new_id,
    normalize_pan_id,
    utcnow,
)
from app.models.enums import DecisionAction


class NotificationChannel(StrEnum):
    """How the operator put the recommendation in front of the farmer.

    One member. This is not a general notification framework and does not pretend
    to be: a second channel added here without a second channel's semantics would be
    a name with no behaviour behind it, and the point of the vocabulary is that
    every member is a thing the system actually does.
    """

    WHATSAPP = "whatsapp"


class DeliveryAction(StrEnum):
    """What pressing the button did.

    ``SEND_INITIATED`` and nothing else. The name is the contract: the record says
    the operator *initiated* a send, which is a fact about a click. Every other
    verb — delivered, received, accepted, acted — describes something that happens
    in WhatsApp or in the field afterwards, somewhere this system cannot observe.
    Naming a state that is not observed is how a communication record becomes an
    evidence record by accident, so the vocabulary has exactly one member, and a
    second would have to be earned by an integration that can actually observe it.
    """

    SEND_INITIATED = "SEND_INITIATED"


class DeliveryStatus(StrEnum):
    """What this system knows about the fate of a sent message.

    One member, and the member is the absence of knowledge. There is no WhatsApp
    Business API integration and no webhook, so nothing downstream of the operator's
    browser press is visible here; ``NOT_CONFIRMED`` is not a placeholder for a
    pending state, it is the permanent state.
    """

    NOT_CONFIRMED = "not_confirmed"


#: Which message layout produced the text, so a later edit to the builder is
#: detectable by comparing this field alone.
MESSAGE_TEMPLATE_VERSION = "whatsapp-recommendation-v1"


class RecommendationDelivery(BaseDocument):
    """One operator-initiated WhatsApp send for one stored recommendation."""

    delivery_id: str = Field(
        default_factory=lambda: new_id("delivery"),
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    recommendation_id: str = Field(min_length=1, max_length=64)
    pan_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")

    #: What was recommended, copied from the recommendation as it stood at the
    #: moment of the send. Copied rather than referenced because recommendations
    #: are superseded and pruned, and an audit of a communication has to show the
    #: text that was actually communicated.
    recommended_action: DecisionAction
    priority: str = Field(min_length=1, max_length=16)
    rule_name: str | None = Field(default=None, max_length=120)
    rule_version: int | None = Field(default=None, ge=1)
    specification_hash: str | None = Field(default=None, max_length=128)

    #: The masked contact. Never the unmasked number — see the module docstring.
    farmer_contact_reference: str = Field(
        min_length=1,
        max_length=32,
        description="The farmer's number masked to its country code and last two digits.",
    )
    farmer_name: str | None = Field(default=None, max_length=128)

    channel: NotificationChannel = NotificationChannel.WHATSAPP
    action: DeliveryAction = DeliveryAction.SEND_INITIATED
    delivery_status: DeliveryStatus = DeliveryStatus.NOT_CONFIRMED

    # -- the three things a send is not -------------------------------------
    # Declared ``Literal[False]`` rather than ``bool``: these are facts about what
    # this system can observe, and a plain ``bool`` would let a future edit or a
    # direct write set one to true. ``Literal`` turns that into a validation error
    # naming the field, which is the outcome this module exists to prevent.
    delivery_confirmed: Literal[False] = False
    farmer_response_recorded: Literal[False] = False
    physical_action_recorded: Literal[False] = False

    message_template_version: str = Field(
        default=MESSAGE_TEMPLATE_VERSION,
        min_length=1,
        max_length=64,
        description="Which message layout produced the text, so a later edit is detectable.",
    )
    message_sha256: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "Hash of the exact message text. The text is not stored; this plus the "
            "stored recommendation is enough to re-derive it and compare."
        ),
    )
    message_character_count: int = Field(ge=0, le=4096)

    operator_id: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Who pressed the button, as supplied by the caller. Null when not "
            "supplied — see the note on authentication in the router docstring."
        ),
    )
    initiated_at: UtcDateTime = Field(default_factory=utcnow)
    created_at: UtcDateTime = Field(default_factory=utcnow)

    @field_validator("pan_id", mode="before")
    @classmethod
    def _normalize_pan_id(cls, value):
        return normalize_pan_id(value)

    @model_validator(mode="after")
    def _a_send_is_never_more_than_a_send(self) -> RecommendationDelivery:
        """Refuse a document claiming anything beyond an initiated send.

        The three booleans are declared with ``Literal[False]``, which already makes
        them impossible to set at the field level. This states the rule in one place
        as well, for the day they are relaxed: a send that recorded a confirmed
        delivery or a recorded physical action would be a false claim about the
        world, and it is better refused at write time than discovered in an audit.
        """
        if self.delivery_confirmed or self.farmer_response_recorded or self.physical_action_recorded:
            raise ValueError(
                "a recommendation delivery records an initiated send only; delivery, "
                "farmer response and physical action are not observed by this system "
                "and cannot be set on this record"
            )
        if self.action is not DeliveryAction.SEND_INITIATED:
            raise ValueError(
                "action must be SEND_INITIATED; no other outcome of a WhatsApp send "
                "is observable here"
            )
        return self


__all__ = [
    "MESSAGE_TEMPLATE_VERSION",
    "DeliveryAction",
    "DeliveryStatus",
    "NotificationChannel",
    "RecommendationDelivery",
]
