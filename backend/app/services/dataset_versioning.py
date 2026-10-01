"""Module 07 - content hashing, so an experiment can be reproduced.

A dataset that cannot be re-derived is an anecdote. This module makes the
identity of a dataset a function of what is in it, so that "the model with
0.91 R2" can be traced to specific rows under a specific specification rather
than to whatever the database happened to hold that afternoon.

The hash is SHA-256 over a canonical JSON encoding of four things:

* the **builder schema version**, so a builder that emits different bytes cannot
  claim the same identity as one that emitted the old ones;
* the **specification** — the named target, its bin edges, the horizon, the
  lookback, the feature list in order and the pan/window filters;
* the **row count**, which keeps an empty dataset from colliding with any other
  empty dataset that happened to share a specification;
* the **rows themselves**, in the canonical order the builder produces.

Three properties are deliberate.

**Order is canonical, not incidental.** Rows are hashed in ``(pan_id, anchor
timestamp, row_id)`` order and object keys are sorted. Without that, the same data
read in a different order would hash differently and the reproducibility claim
would be a claim about Mongo's sort stability rather than about the dataset.

**Floats are hashed as the shortest string that round-trips.** Python's ``repr``
is that string, so ``json.dumps`` is already exact and no rounding is applied. A
hash that quietly rounded a salinity to three decimals would let two genuinely
different datasets share an identity.

**The name is not hashed.** A dataset's ``name`` is a human label; renaming it
does not change what is in it, so including it would produce a spurious "new
dataset" for a rename.

What is deliberately *not* hashed: the report. It is derived from the rows, so
hashing it as well would add nothing but a second place for the two to disagree.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from app.models.base import normalize_datetime

#: Prefix on every stored hash, so the algorithm is readable from the value
#: itself rather than inferred from the field it happens to sit in.
HASH_ALGORITHM = "sha256"

#: The shape every stored hash must match. Lives here rather than beside either
#: model that stores one, because a dataset's content hash and a risk definition's
#: specification hash are the same kind of value and either model may need to
#: validate the other's.
HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"

#: How much of a hex digest to show in logs, filenames and the UI. Twelve
#: characters is far more than enough to tell two datasets apart by eye and far
#: too little to be mistaken for the full digest.
SHORT_HASH_LENGTH = 12

#: Characters used when building a row identifier from a pan and an instant.
#: Chosen because ``new_id`` generates the same shape, so a row identifier and a
#: document identifier are never confused for one another.
_ROW_ID_DIGEST_LENGTH = 10


def _canonical(value: Any) -> Any:
    """Convert a value into something ``json.dumps`` writes identically every time.

    Datetimes become ISO 8601 in UTC, because the same instant has several
    textual spellings and a hash must not depend on which one a driver produced.
    Enums become their stored value. Everything else is left to ``json``.
    """
    if isinstance(value, datetime):
        return normalize_datetime(value).isoformat()
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # A StrEnum is a str subclass, so the branch above would take it; this one
    # catches anything else JSON cannot represent, and refuses rather than
    # stringifying it into a hash nobody can reproduce.
    if hasattr(value, "value"):
        return _canonical(value.value)
    raise TypeError(
        f"cannot canonicalise a value of type {type(value).__name__} for hashing; "
        "a dataset row may only contain scalars, datetimes, lists and mappings"
    )


def canonical_json(payload: Any) -> str:
    """Encode a payload so that equal data always produces equal bytes.

    Sorted keys, no insignificant whitespace, and no ASCII escaping: the digest
    is taken over UTF-8, so an escaped and an unescaped spelling of the same text
    would otherwise hash differently for no reason.
    """
    return json.dumps(
        _canonical(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def content_hash(payload: Any) -> str:
    """Return the prefixed SHA-256 digest of a canonical payload."""
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"{HASH_ALGORITHM}:{digest}"


def short_hash(digest: str) -> str:
    """The leading hex characters of a hash, without its algorithm prefix."""
    return digest.split(":", 1)[-1][:SHORT_HASH_LENGTH]


def row_id_for(pan_id: str, anchor: datetime) -> str:
    """A stable identifier for one row.

    Derived from the pan and the anchor instant rather than generated, so two
    builds of the same data name the same rows. That is what lets a diff between
    two exports show which rows actually changed instead of showing every row as
    new.
    """
    normalized = normalize_datetime(anchor).isoformat()
    seed = f"{pan_id}|{normalized}".encode()
    return f"row-{hashlib.sha256(seed).hexdigest()[:_ROW_ID_DIGEST_LENGTH]}"


def hash_inputs(
    *,
    builder_schema_version: int,
    specification: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the exact payload a dataset's identity is computed from.

    Kept as a function so the builder cannot accidentally hash a subset of what
    defines it, and so a test can hash a known payload and check the digest
    against an independently computed value.
    """
    return {
        "builder_schema_version": builder_schema_version,
        "specification": specification,
        "row_count": len(rows),
        "rows": rows,
    }


__all__ = [
    "HASH_ALGORITHM",
    "HASH_PATTERN",
    "SHORT_HASH_LENGTH",
    "canonical_json",
    "content_hash",
    "hash_inputs",
    "row_id_for",
    "short_hash",
]
