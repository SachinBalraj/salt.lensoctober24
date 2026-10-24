"""E.164 phone numbers: one normaliser, one validator, one mask.

A phone number is the only personal datum this project stores about a person, and
every handling rule for it belongs in one module so that no caller can invent its
own. Three things live here and nothing else does.

**Normalisation.** ``+91 98765 43210``, ``+91-98765-43210`` and ``+919876543210``
are one number written three ways. WhatsApp's ``click-to-chat`` URL takes the
digits and nothing else, so a stored value carrying spaces produces a URL that
opens an empty chat rather than the intended one. Normalisation happens once, at
the boundary, so nothing downstream has to know which way it was typed.

**Validation.** E.164, and nothing looser. A leading ``+``, then 8 to 15 digits.
Not 8 to 15 digits *without* a ``+``: a national number with no country code is
ambiguous between countries, and a click-to-chat URL built from one silently
messages a stranger with a similar number. The bounds are E.164's own, so this is
not a project preference dressed as a standard.

The normaliser returns the canonical ``+``-prefixed string rather than raising, so
the same function serves a validator (which turns an ``InvalidWhatsAppNumber``
into a 422) and a read path that wants to know whether a stored number is still
usable.

**Masking.** :func:`mask_phone` is the only sanctioned way to show or log a
number, and it keeps the country code and the final two digits only. That is the
same convention WhatsApp itself uses in a contact row, and it is enough for an
operator to recognise "the number I filed for North Pan" without the log becoming
a directory of every farmer in the deployment.

The mask is a *reference*, not a lookup key. :func:`mask_phone` is deterministic
on the last two digits, so two farmers on the same network are indistinguishable
in a log; that is accepted, because the alternative — a keyed hash of the number —
would make the log reversible by anyone who can guess ten digits.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BeforeValidator

#: E.164 as ITU-T E.164 defines it: a ``+``, then 8 to 15 digits. The upper bound
#: is 15 because that is the longest a country code plus subscriber number may be.
E164_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")

#: Separators a person types between digit groups. Stripped rather than rejected,
#: because rejecting ``+91 98765 43210`` teaches an operator the field is broken
#: when what they typed is the number.
_SEPARATORS = re.compile(r"[\s()\-.]")

#: What a masked number keeps. The country code is what an operator uses to tell
#: two configured numbers apart; the last two digits are what they recognise.
_MASK_KEEP_PREFIX = 3
_MASK_KEEP_SUFFIX = 2


class InvalidWhatsAppNumber(ValueError):
    """A value that is not a usable E.164 phone number.

    Carries the reason separately from the message so a caller can distinguish
    "you left it blank" from "that is not a number I can dial", which are
    different things for an operator to fix.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def normalize_phone(value: str | None) -> str | None:
    """Return the canonical ``+``-prefixed form of a phone number.

    ``None`` and a blank string both mean *not configured*, and both return
    ``None``. That distinction is the whole reason this returns a nullable: a pan
    whose farmer has no number on file is a normal state to display, not an error,
    and it must stay distinguishable from a number that was typed and rejected.

    Raises :class:`InvalidWhatsAppNumber` for anything present but not E.164.
    """
    if value is None:
        return None
    stripped = _SEPARATORS.sub("", value.strip())
    if not stripped:
        return None
    if not stripped.startswith("+"):
        raise InvalidWhatsAppNumber(
            "a phone number must start with + and the country code, for example "
            "+919876543210"
        )
    if not stripped[1:].isdigit():
        raise InvalidWhatsAppNumber(
            "a phone number may contain only digits after the +"
        )
    if not E164_PATTERN.match(stripped):
        raise InvalidWhatsAppNumber(
            "a phone number must be 8 to 15 digits after the +, including the "
            "country code"
        )
    return stripped


#: An optional E.164 number for a pydantic field or model.
#:
#: Optional because "no number on file" is a normal state for a pan whose farmer
#: is unknown, and it must stay representable. The ``BeforeValidator`` is what
#: turns a normalisation failure into a 422 at the API boundary, so no caller ever
#: has to remember to call :func:`normalize_phone` itself — and therefore no caller
#: can forget and store an unnormalised value.
WhatsAppNumber = Annotated[str | None, BeforeValidator(normalize_phone)]


def mask_phone(value: str | None) -> str | None:
    """A recognisable, non-contactable form of a number, for logs and audit rows.

    Keeps the country code and the final two digits, replacing the rest with a
    bullet. ``None`` in, ``None`` out: masking an unconfigured number would
    produce a string that looks like a recorded contact without being one.

    Returns ``None`` for a value that does not normalise, because a mask is for
    showing what was already stored, and inventing one from an unusable value
    would put a number-shaped string into a log that no number was ever filed for.
    """
    try:
        normalized = normalize_phone(value)
    except InvalidWhatsAppNumber:
        return None
    if normalized is None:
        return None
    digits = normalized[1:]
    if len(digits) <= _MASK_KEEP_PREFIX + _MASK_KEEP_SUFFIX:
        # Too short to keep any prefix and any suffix without revealing the whole
        # number, so nothing is kept.
        return f"+{'•' * len(digits)}"
    return (
        f"+{digits[:_MASK_KEEP_PREFIX]}"
        f"{'•' * (len(digits) - _MASK_KEEP_PREFIX - _MASK_KEEP_SUFFIX)}"
        f"{digits[-_MASK_KEEP_SUFFIX:]}"
    )


__all__ = ["E164_PATTERN", "InvalidWhatsAppNumber", "WhatsAppNumber", "mask_phone", "normalize_phone"]
