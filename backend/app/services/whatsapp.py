"""The WhatsApp message, and the click-to-chat URL it becomes.

**Every figure in the message is read from the stored recommendation.** Nothing
here computes a salinity, re-evaluates a criterion, or re-derives an action. The
recommendation was made by Module 12 from the twin, the mass balance and a
versioned rule; this module reads that document and writes it out in words. A
second implementation of the recommendation logic here would be a second thing that
could disagree with the first, and the message would then be able to recommend
something the stored record does not.

**A value that is absent is omitted, not filled.** If the recommendation carries no
simulation block, there is no predicted-salinity line. If the rule recorded no
threshold, there is no threshold line. A blank line would read as a value of zero;
a line reading "n/a" would read as a finding. Both are worse than the line not
being there, and the audit record's character count is the only consumer that
cares.

**The message never claims what it cannot know.** It says the recommendation is
decision support and asks the farmer to verify conditions. It does not say the
harvest will succeed, does not describe a simulated salinity as a measurement, and
does not state or imply that anything has been delivered, received or agreed — the
system learns none of that from a click-to-chat URL. See
:mod:`app.models.recommendation_delivery` for the record that is written when the
operator presses the button, and for why it can only say ``SEND_INITIATED``.

**Click-to-chat is not a delivery API.** The URL is WhatsApp's own documented
``wa.me`` scheme: the browser opens it, WhatsApp hands the message to the operator's
own chat window, and the operator presses send there. No WhatsApp Business account,
token or webhook is involved, so there is nothing in this module to configure and
nothing that could be mistaken for a configured integration. There is deliberately
no client-visible flag claiming delivery.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

from app.models.phone import mask_phone, normalize_phone
from app.models.recommendation import Recommendation
from app.models.recommendation_delivery import MESSAGE_TEMPLATE_VERSION
from app.models.salt_pan import SaltPan

#: WhatsApp's click-to-chat scheme. This is a link the operator's own browser
#: follows into WhatsApp — not an API endpoint this project calls.
WHATSAPP_CLICK_TO_CHAT_BASE = "https://wa.me/"

#: The exact line that tells the farmer what this is and is not. Held as a constant
#: rather than composed per send so that two messages compared in an audit differ
#: only where the recommendation differs, and a reader can see the disclaimer is
#: fixed rather than edited per recipient.
VERIFICATION_DISCLAIMER = (
    "This is a decision-support recommendation, not a measured field outcome. "
    "Please verify the current field conditions before taking any physical action."
)

_HEADER = "SaltLens DSS - Salt-pan recommendation"

_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

_PRIORITY_LABELS = {"low": "Low", "medium": "Medium", "high": "High"}


@dataclass(frozen=True)
class WhatsAppMessage:
    """The text that will be pre-filled, and the facts it was built from.

    ``text`` is what goes on the wire. ``salinity_g_l`` and ``threshold_g_l`` are
    kept alongside it so the audit record and the tests can assert on the figures
    without re-parsing prose, and ``lines`` records which optional sections were
    actually present — so "the threshold was omitted because there was none" is
    distinguishable from "the builder forgot the threshold".
    """

    text: str
    lines: tuple[str, ...]
    template_version: str
    salinity_g_l: float | None = None
    threshold_g_l: float | None = None
    rainfall_mm: float | None = None

    @property
    def sha256(self) -> str:
        """Hash of the exact text, for the audit record.

        Not a security hash and not used as one. Its job is to answer "was this the
        same message as last time?" six months later, given that the recommendation
        it came from is stored and can be re-derived — so a change in the builder
        shows up as a changed hash even when the recommendation did not change.
        """
        return "sha256:" + hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def character_count(self) -> int:
        return len(self.text)


def _format_date(value: datetime) -> str:
    """``14 March 2026`` — the form the field record already uses."""
    return f"{value.day} {_MONTHS[value.month - 1]} {value.year}"


def _format_salinity(value: float) -> str:
    """Two decimals, trailing zeros dropped.

    ``222.22`` rather than ``222.22222222222223``: the figure is a simulated
    salinity shown to a person deciding whether to harvest, and the extra digits
    are float noise being presented as precision. The full value stays on the
    recommendation.
    """
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _salinity_threshold(recommendation: Recommendation, pan: SaltPan) -> float | None:
    """The configured figure a salinity reading is judged against.

    Taken from the rule's own matched criteria where one named a threshold, and
    otherwise from the pan's registered harvest target. The first is preferred
    because it is the limit the rule actually fired on; the second is the static
    property the catalogue exposes for exactly this comparison. ``None`` when
    neither exists, and the line is then omitted.
    """
    if recommendation.evaluation is not None:
        for match in recommendation.evaluation.matched_criteria:
            threshold = match.threshold
            if isinstance(threshold, (int, float)) and "salinity" in match.variable:
                return float(threshold)
    return pan.target_harvest_salinity_g_l


def build_message(recommendation: Recommendation, pan: SaltPan) -> WhatsAppMessage:
    """Render one stored recommendation as the text a farmer is sent.

    Both arguments are read-only. The builder holds no state and calls no service,
    which is what makes "the message cannot disagree with the recommendation" a
    structural property rather than a review item.
    """
    lines: list[str] = [_HEADER, ""]

    # Pan and date. The pan name comes from the pan document rather than the
    # recommendation because the recommendation stores only the identifier — a
    # pan renamed after the recommendation was issued is named as it is now, which
    # is the name the farmer knows it by.
    lines.append(f"Pan: {pan.pan_name}")
    lines.append(f"Date: {_format_date(recommendation.issued_at)}")
    lines.append("")

    # The recommendation itself: the action from the closed vocabulary, verbatim,
    # and its priority.
    action = recommendation.action
    if action is not None:
        lines.append(f"Recommendation: {action.value}")
        lines.append(
            f"Priority: {_PRIORITY_LABELS.get(recommendation.priority.value, recommendation.priority.value.title())}"
        )
        lines.append("")

    # Figures. Each is conditional on the recommendation carrying it. The salinity
    # is labelled as simulated because that is what it is — the mass balance's
    # output — and a farmer reading "predicted salinity" beside a physical gauge
    # reading has no way to tell the difference otherwise.
    salinity: float | None = None
    threshold: float | None = None
    rainfall: float | None = None

    if recommendation.simulation is not None:
        salinity = recommendation.simulation.post_rain_salinity_g_l
        rainfall = recommendation.simulation.rainfall_mm

    threshold = _salinity_threshold(recommendation, pan)

    if salinity is not None:
        lines.append(f"Predicted salinity after rain (simulated): {_format_salinity(salinity)} g/L")
    if threshold is not None:
        lines.append(f"Configured harvest threshold: {_format_salinity(threshold)} g/L")
    if rainfall is not None:
        source = recommendation.simulation.rainfall_source if recommendation.simulation else ""
        suffix = f" ({source})" if source else ""
        lines.append(f"Rainfall scenario: {_format_salinity(rainfall)} mm{suffix}")

    # The rule that decided. A recommendation without its rule version is an
    # instruction rather than a recommendation, and the farmer is entitled to know
    # which rule produced the words they are being asked to act on.
    if recommendation.rule_name is not None:
        version = (
            f" v{recommendation.rule_version}"
            if recommendation.rule_version is not None
            else ""
        )
        lines.append(f"Recommendation rule: {recommendation.rule_name}{version}")

    # Deliberately absent: the stored model's probability, where one exists.
    #
    # A figure like "82% chance of salinity" read on a phone, by someone deciding
    # whether to open a gate today, is the single number in the recommendation most
    # likely to be misread as certainty — and it is a figure Module 12 itself holds
    # at arm's length, reporting it separately from the action and labelling a null
    # probability with its reason. Reproducing it in a message addressed to a person
    # would undo that separation at the point where it can do the most damage. The
    # action carries the recommendation; the evidence behind it stays in the
    # application where its kind is stated.

    lines.append("")
    lines.append(VERIFICATION_DISCLAIMER)

    return WhatsAppMessage(
        text="\n".join(lines),
        lines=tuple(lines),
        template_version=MESSAGE_TEMPLATE_VERSION,
        salinity_g_l=salinity,
        threshold_g_l=threshold,
        rainfall_mm=rainfall,
    )


def build_link_url(phone: str, message_text: str) -> str:
    """The ``wa.me`` URL for arbitrary text addressed to one number.

    Split out from :func:`build_click_to_chat_url` so the link built from an
    operator-composed message (see ``app.services.whatsapp_delivery``) and the link
    built from a stored recommendation share one encoding step. The number is
    normalised rather than trusted, so a caller that passes a value straight from a
    request cannot get a URL containing spaces. ``quote`` is called with ``safe=""``
    because the text contains spaces, plus signs, ampersands and newlines, and any
    of those left unencoded would either truncate the text at the first ``&`` or
    reach WhatsApp as a different message than the one that was shown.
    """
    normalized = normalize_phone(phone)
    if normalized is None:
        raise ValueError("a phone number is required to build a WhatsApp URL")
    digits = normalized[1:]
    return f"{WHATSAPP_CLICK_TO_CHAT_BASE}{digits}?text={quote(message_text, safe='')}"


def build_click_to_chat_url(phone: str, message: WhatsAppMessage) -> str:
    """The ``wa.me`` URL that opens WhatsApp with a stored message pre-filled.

    Thin wrapper over :func:`build_link_url`; the template's text is what is
    encoded, so the URL and the audit hash describe the same bytes.
    """
    return build_link_url(phone, message.text)


def contact_reference(pan: SaltPan) -> str | None:
    """The masked number for the audit record, or ``None`` when unconfigured."""
    return mask_phone(pan.farmer_whatsapp)


__all__ = [
    "VERIFICATION_DISCLAIMER",
    "WHATSAPP_CLICK_TO_CHAT_BASE",
    "WhatsAppMessage",
    "build_click_to_chat_url",
    "build_link_url",
    "build_message",
    "contact_reference",
]
