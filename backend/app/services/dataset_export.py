"""Module 07 - CSV export of a dataset.

One file, one row per observation instant, columns in catalogue order, and a
header a spreadsheet or a pandas ``read_csv`` can both read without being told
what anything means. The manifest beside it says what the columns mean; the file
itself says what the values are.

The rule that makes the file honest is the treatment of an empty cell.

**An empty cell means the value was never recorded. ``0`` means it was.** That
distinction survives CSV, which is why nothing here writes a zero, a mean or a
carry-forward into a gap. A reader who loads the file with a library that turns
empty cells into NaN gets the right answer, and a reader who inspects the raw text
can still tell a missing salinity from a measured 0 g/L.

The exported file is derived from the stored records at the moment of the request
rather than from a stored snapshot, so it can never quietly go stale. That has one
consequence worth stating: if an operator has backfilled a reading since the
dataset was built, the file differs from the one the manifest's hash describes.
The export therefore reports both hashes, and the endpoint sets a header saying
whether they still agree, rather than handing over a file whose provenance the
caller has to guess.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from typing import Any

from app.models.base import normalize_datetime
from app.services.dataset_builder import BuiltDataset, DatasetRow
from app.services.dataset_catalog import ANCHOR_KEY

#: Columns written for every row, before the features.
#:
#: The anchor instant comes first because it is what every other column is
#: relative to, and ``is_labelled`` comes before the label so a reader scrolling
#: the file sees whether a row is trainable before they see what it is.
KEY_COLUMNS: tuple[str, ...] = (
    "row_id",
    "pan_id",
    ANCHOR_KEY,
    "target_measured_at",
    "target_source_id",
)

#: Columns written after the features.
TRAILING_COLUMNS: tuple[str, ...] = ("is_labelled", "missing_features")

#: How a null is written. An empty cell, never a sentinel string: ``NA``, ``None``
#: and ``null`` are all values a real measurement could conceivably be read as,
#: and an empty cell is not.
NULL_CELL = ""


def _format(value: Any) -> str:
    """Render one cell.

    Datetimes become ISO 8601 in UTC so a round trip through the file preserves
    the instant rather than the offset it happened to be written with. Booleans
    become ``true``/``false`` because that is what every CSV consumer expects.
    Floats use ``repr``, the shortest string that reads back as the same double,
    so the file does not quietly round a salinity.
    """
    if value is None:
        return NULL_CELL
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, int):
        return str(value)
    if hasattr(value, "isoformat"):
        return normalize_datetime(value).isoformat()
    return str(value)


def column_order(built: BuiltDataset) -> list[str]:
    """The file's column order: keys, features, the label, and what qualifies it.

    For a rule-derived target the rule's own provenance is written next to the
    label rather than left out. A file whose ``label_operational_risk`` column
    names a severity but carries no rule, version or source variables cannot be
    checked against anything, and the whole point of a rule-derived label is that
    it is attributable -- dropping these columns would export a severity
    vocabulary stripped of the argument that produced it, which is the one thing
    this module exists to prevent.
    """
    label = [built.target.label_column]
    if built.spec.risk_definition is not None:
        label += list(DatasetRow.RISK_PROVENANCE_COLUMNS)
    return (
        list(KEY_COLUMNS)
        + [feature.name for feature in built.features]
        + label
        + list(TRAILING_COLUMNS)
    )


def rows_in_file_order(built: BuiltDataset) -> list[DatasetRow]:
    """Rows sorted the way the hash sorted them, so the file matches the digest."""
    return sorted(
        built.rows, key=lambda row: (row.pan_id, row.anchor_timestamp, row.row_id)
    )


def to_csv(built: BuiltDataset) -> str:
    """Render a built dataset as CSV text.

    Line endings are ``\\n`` rather than the platform default, so a file built on
    one machine hashes to the same digest as one built on another.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(column_order(built))

    feature_names = [feature.name for feature in built.features]
    for row in rows_in_file_order(built):
        flat = row.as_flat_row(feature_names)
        writer.writerow(
            [_format(flat.get(column)) for column in column_order(built)]
        )
    return buffer.getvalue()


def to_csv_bytes(built: BuiltDataset) -> bytes:
    """The same file, UTF-8 encoded, ready for a response body."""
    return to_csv(built).encode("utf-8")


def export_filename(built: BuiltDataset, short_hash: str) -> str:
    """A filename that says which dataset the file is.

    The target name and the short hash are both in it, so a file found on a
    desktop six months later still identifies its own provenance without opening
    it.
    """
    return f"saltlens-{built.target.name.value}-{short_hash}.csv"


def summarise_rows(built: BuiltDataset, limit: int) -> list[dict[str, Any]]:
    """The first rows as plain mappings, for a JSON preview.

    The preview is built from the same :class:`DatasetRow` objects as the CSV, so
    what a caller sees in the browser and what lands in the file are the same
    numbers rather than two renderings that can drift.
    """
    feature_names = [feature.name for feature in built.features]
    return [row.as_flat_row(feature_names) for row in rows_in_file_order(built)[:limit]]


def preview_columns(built: BuiltDataset) -> Sequence[str]:
    """The columns a preview shows, in file order."""
    return tuple(column_order(built))


__all__ = [
    "KEY_COLUMNS",
    "NULL_CELL",
    "TRAILING_COLUMNS",
    "column_order",
    "export_filename",
    "preview_columns",
    "rows_in_file_order",
    "summarise_rows",
    "to_csv",
    "to_csv_bytes",
]
