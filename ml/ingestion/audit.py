"""Source-file ingestion - what a file actually contains, before anything is stored.

An audit is a set of statements a human can check, not a pass mark. Every finding
carries a severity and the rows it came from, because the difference between "this
column is 40% empty" and "this column is the salinity" is the difference between a
report a researcher acts on and a row count.

**Two severities, and the strict one is about the file's claim to be a measurement.**
An ``error`` means a record cannot be written: a required field is absent, a value
falls outside the range the model itself enforces, or a value is outside a closed
vocabulary. A ``warning`` means the record *can* be written but a person should look
at it first: an unrecognised column, a duplicate, a pan identifier that had to be
folded, a column that is entirely empty.

**Nothing here repairs a file.** No imputation, no unit guessing, no coercion of an
out-of-range number to the nearest in-range one, no forward fill. A field nobody
measured stays absent in the output, which is the same rule the models already
enforce -- ``pan_observations`` stores a missing measurement as ``None`` and the
dataset builder reports it as missing rather than filling it in. An audit that
quietly fixed its input could not then be used to argue that the stored data is
what the researcher handed over.
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .catalog import (
    ProvenanceCategory,
    RecordSpec,
    _model_bounds,
    canonical_row,
    content_id,
    file_digest,
    normalize_pan,
)

#: Severities, in the order a report lists them.
SEVERITIES = ("error", "warning")

#: Value a numeric column takes when it holds nothing at all. ``None`` is used
#: rather than ``0`` deliberately: a zero salinity and an unmeasured salinity are
#: different claims, and an audit that counted them together would report a
#: complete column where the file has none.
_MISSING = object()

#: An explicit timezone offset at the end of a timestamp, as opposed to the
#: hyphens inside a date. ``2026-01-01T00:00:00`` has none; ``...+05:30`` does.
_OFFSET = re.compile(r"[+-]\d{2}:?\d{2}$")

#: The identifier rule the models themselves enforce, quoted rather than restated so
#: a change to ``PAN_ID_PATTERN`` is followed here instead of diverging from it.
from app.models.pan_observation import PAN_ID_PATTERN as _PAN_ID_PATTERN

_PAN_ID_RE = re.compile(_PAN_ID_PATTERN)


@dataclass(frozen=True)
class Finding:
    """One thing about a file that a person should look at."""

    severity: str
    code: str
    message: str
    rows: tuple[int, ...] = ()
    column: str | None = None

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "rows": list(self.rows),
            "column": self.column,
        }


@dataclass(frozen=True)
class ColumnAudit:
    """What one source column turned out to contain."""

    source_name: str
    canonical_name: str | None
    present: int
    non_null: int
    distinct: int
    numeric: bool
    minimum: float | None = None
    maximum: float | None = None
    unit: str | None = None
    examples: tuple[str, ...] = ()

    @property
    def missing(self) -> int:
        return self.present - self.non_null

    @property
    def missing_fraction(self) -> float:
        return self.missing / self.present if self.present else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "canonical_name": self.canonical_name,
            "present": self.present,
            "non_null": self.non_null,
            "missing": self.missing,
            "missing_fraction": round(self.missing_fraction, 4),
            "distinct": self.distinct,
            "numeric": self.numeric,
            "min": self.minimum,
            "max": self.maximum,
            "unit": self.unit,
            "examples": list(self.examples),
        }


@dataclass
class FileAudit:
    """Everything known about one source file."""

    path: Path
    kind: str
    collection: str
    content_hash: str
    row_count: int
    provenance: str | None
    columns: list[ColumnAudit] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    distinct_pans: tuple[str, ...] = ()
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    parsable: bool = True

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == "error")

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == "warning")

    @property
    def accepted(self) -> bool:
        """Whether this file may be staged. A single error blocks the whole file.

        All-or-nothing rather than partial, because a partially-loaded file leaves
        a researcher unable to say which rows are in the database. The rejected rows
        are named in the findings so the fix is obvious.
        """
        return self.parsable and not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "kind": self.kind,
            "collection": self.collection,
            "content_hash": self.content_hash,
            "row_count": self.row_count,
            "provenance": self.provenance,
            "distinct_pans": list(self.distinct_pans),
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
            "accepted": self.accepted,
            "columns": [c.as_dict() for c in self.columns],
            "findings": [f.as_dict() for f in self.findings],
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
        }


def read_rows(path: Path) -> list[dict[str, Any]]:
    """Read a source file into a list of dicts, preserving string values.

    Values are left as the file's strings. That is not laziness: a CSV has no types,
    and deciding here that ``"285"`` is a number and ``"2.85e2"`` is too would be a
    guess about a measurement. Coercion happens once, in
    :func:`~ml.ingestion.validate.parse_record`, against a declared field, where a
    failure can be reported against a row number.

    Only the three formats a researcher realistically exports are supported. A
    format this function does not recognise is an error naming the suffix, not a
    silent attempt to guess.
    """
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            # A single object is a one-row file. Accepting it avoids making a
            # researcher wrap one measurement in a list to satisfy a parser.
            return [payload]
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        raise ValueError(
            f"{path.name}: a .json source must be an object or a list of objects, "
            f"not {type(payload).__name__}"
        )
    if suffix in {".jsonl", ".ndjson"}:
        rows: list[dict[str, Any]] = []
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name} line {number}: {exc}") from exc
            if not isinstance(parsed, dict):
                raise TypeError(
                    f"{path.name} line {number}: each line must be a JSON object"
                )
            rows.append(parsed)
        return rows
    raise ValueError(
        f"{path.name}: unsupported source format {suffix!r}. Supported: .csv, "
        ".json, .jsonl, .ndjson"
    )


def _is_blank(value: Any, vocabulary: tuple[str, ...] | None = None) -> bool:
    """Whether a source cell counts as absent.

    Covers the three ways a file spells "not recorded": an empty string, a
    whitespace-only string, and the literal words a spreadsheet writes for a
    formula that was never filled in. Treating ``N/A`` as a number would be a
    fabrication, and treating it as absent is the only honest reading.

    ``vocabulary`` is checked first, and this is not a detail. ``HarvestOutcome``
    contains the member ``none``, and a recorded ``none`` is a label -- "nothing was
    harvested" -- while a blank cell is an absence. Reading the word ``none`` as
    absent would drop the label the model goes out of its way to distinguish from
    ``null``, and would do it silently, on exactly the rows that carry the most
    information. A closed vocabulary is therefore the authority on what its own
    member values mean, and the generic blank list applies only to everything else.
    """
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    folded = value.strip().lower()
    if vocabulary and folded in {member.strip().lower() for member in vocabulary}:
        return False
    return folded in {"", "na", "n/a", "nan", "null", "none", "-", "--"}


def _as_number(value: Any) -> float | None:
    """Parse a numeric cell, or return ``None`` if it is not one.

    Returns ``None`` for a non-numeric string rather than raising, so the caller
    can report it against a row number. A value that is not a number is a finding,
    not a crash: the rest of the file may still be worth auditing.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if math.isnan(float(value)) or math.isinf(float(value)) else float(value)
    if not isinstance(value, str):
        return None
    folded = value.strip().replace(",", "")
    if not folded:
        return None
    try:
        parsed = float(folded)
    except ValueError:
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse a timestamp, assuming UTC when the file does not say.

    The UTC default is stated rather than inferred because it is a decision: a
    field notebook with no zone is almost always local time, and a reader of the
    audit has to be told which was assumed. :func:`audit_rows` reports the count,
    so a file full of naive timestamps is visible rather than silently shifted.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%m/%d/%Y"):
            try:
                # These patterns carry no offset, so the reading is stamped UTC
                # as it is parsed rather than left naive and corrected later.
                parsed = datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        else:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def audit_rows(
    path: Path,
    spec: RecordSpec,
    rows: Sequence[dict[str, Any]],
    *,
    provenance: str | None = None,
) -> FileAudit:
    """Audit parsed rows against a record specification."""
    audit = FileAudit(
        path=path,
        kind=spec.name,
        collection=spec.collection,
        content_hash=file_digest(path),
        row_count=len(rows),
        provenance=provenance,
    )

    if not rows:
        audit.findings.append(
            Finding("warning", "empty_file", f"{path.name} contains no data rows.")
        )
        return audit

    # -- provenance ---------------------------------------------------------
    if provenance is None:
        audit.findings.append(
            Finding(
                "error",
                "provenance_missing",
                "No provenance declared. Pass --provenance so each row records how "
                "it was obtained; a row with no declared origin cannot be audited "
                "later by anyone but this run.",
            )
        )
    elif provenance not in ProvenanceCategory.ALL:
        audit.findings.append(
            Finding(
                "error",
                "provenance_unknown",
                f"Unknown provenance category {provenance!r}. Expected one of: "
                + ", ".join(ProvenanceCategory.ALL),
            )
        )
    elif provenance in ProvenanceCategory.DERIVED:
        audit.findings.append(
            Finding(
                "error",
                "provenance_not_measurement",
                f"Provenance {provenance} is a computation, not a measurement, so "
                f"these rows cannot be written to {spec.collection}. A physics "
                "prediction belongs in a simulation run and a simulated row belongs "
                "in an experiment fixture that says so on every row.",
            )
        )
    elif provenance == ProvenanceCategory.UNKNOWN:
        audit.findings.append(
            Finding(
                "error",
                "provenance_unknown_category",
                "Provenance 'UNKNOWN' is a declaration of absence. Name how the rows "
                "were obtained, or leave the file out of the research database.",
            )
        )

    # -- column shape -------------------------------------------------------
    all_columns: list[str] = []
    for row in rows:
        for column in row:
            if column not in all_columns:
                all_columns.append(column)

    unknown = spec.unknown_columns(all_columns)
    if unknown:
        audit.findings.append(
            Finding(
                "warning",
                "unmapped_columns",
                f"{len(unknown)} column(s) matched no SaltLens field and will not be "
                "stored: " + ", ".join(repr(c) for c in unknown),
                column=", ".join(unknown),
            )
        )

    # -- per-column and per-row findings ------------------------------------
    bounds = spec.by_name
    for source_name in all_columns:
        canonical_name = spec.resolve(source_name)
        raw_values = [row.get(source_name, _MISSING) for row in rows]
        field_spec = bounds.get(canonical_name) if canonical_name else None
        # The closed vocabulary, when the field has one. Consulted by ``_is_blank``
        # so that a member value such as ``none`` is read as a value and not as an
        # absence.
        closed = field_spec.vocabulary if field_spec else None
        non_blank = [v for v in raw_values if not _is_blank(v, closed)]

        # The bounds a value in *this* column is checked against. For a flat column
        # feeding a nested model these are the inner field's own bounds, read from
        # the nested model rather than from a second table here, so the check cannot
        # drift from the constraint validation will apply.
        minimum = field_spec.minimum if field_spec else None
        maximum = field_spec.maximum if field_spec else None
        strict_min = field_spec.strict_minimum if field_spec else False
        strict_max = field_spec.strict_maximum if field_spec else False
        numeric_field = field_spec is not None and field_spec.value_kind == "numeric"
        component = spec.component_field(source_name)
        if component is not None and field_spec is not None and field_spec.nested_model:
            inner = component[1]
            if inner in field_spec.nested_model.model_fields:
                minimum, maximum, strict_min, strict_max = _model_bounds(
                    field_spec.nested_model, inner
                )
                numeric_field = True

        numbers: list[float] = []
        non_numeric_rows: list[int] = []
        for index, value in enumerate(raw_values, start=1):
            if _is_blank(value, closed):
                continue
            number = _as_number(value)
            if number is None:
                non_numeric_rows.append(index)
            else:
                numbers.append(number)

        numeric = bool(numbers) and not non_numeric_rows
        is_numeric_field = numeric_field
        if non_numeric_rows and is_numeric_field:
            audit.findings.append(
                Finding(
                    "error",
                    "non_numeric_measurement",
                    f"{source_name!r} holds {len(non_numeric_rows)} value(s) that are "
                    f"not numbers, so they cannot be read as {canonical_name}. The "
                    "value is left absent rather than guessed at.",
                    rows=tuple(non_numeric_rows[:20]),
                    column=source_name,
                )
            )

        if numeric and field_spec is not None and (minimum is not None or maximum is not None):
            below = [
                i
                for i, v in enumerate(raw_values, start=1)
                if (n := _as_number(v)) is not None
                and minimum is not None
                and (n < minimum or (strict_min and n == minimum))
            ]
            above = [
                i
                for i, v in enumerate(raw_values, start=1)
                if (n := _as_number(v)) is not None
                and maximum is not None
                and (n > maximum or (strict_max and n == maximum))
            ]
            if below or above:
                detail = []
                if below:
                    detail.append(f"{len(below)} below {minimum:g}")
                if above:
                    detail.append(f"{len(above)} above {maximum:g}")
                inner_label = f" -> {canonical_name}" if component is not None else ""
                audit.findings.append(
                    Finding(
                        "error",
                        "out_of_range",
                        f"{source_name!r}{inner_label} has values outside the "
                        f"range the model accepts ({', '.join(detail)}). Check the "
                        f"units: the field is {field_spec.unit or 'unspecified'}.",
                        rows=tuple((below + above)[:20]),
                        column=source_name,
                    )
                )

        if field_spec is not None and field_spec.vocabulary:
            bad_rows: list[int] = []
            bad_values: set[str] = set()
            for index, value in enumerate(raw_values, start=1):
                if _is_blank(value, closed):
                    continue
                text = str(value).strip()
                if text not in field_spec.vocabulary:
                    bad_rows.append(index)
                    bad_values.add(text)
            if bad_rows:
                audit.findings.append(
                    Finding(
                        "error",
                        "outside_vocabulary",
                        f"{source_name!r} holds {len(bad_rows)} value(s) outside the "
                        f"closed vocabulary {field_spec.vocabulary}: "
                        + ", ".join(sorted(repr(v) for v in bad_values)),
                        rows=tuple(bad_rows[:20]),
                        column=source_name,
                    )
                )

        if not non_blank:
            audit.findings.append(
                Finding(
                    "warning",
                    "empty_column",
                    f"{source_name!r} is present but empty in every row.",
                    column=source_name,
                )
            )

        examples = tuple(
            str(v) for v in non_blank[:3]
        )
        audit.columns.append(
            ColumnAudit(
                source_name=source_name,
                canonical_name=canonical_name,
                present=len(rows),
                non_null=len(non_blank),
                distinct=len({str(v) for v in non_blank}),
                numeric=numeric,
                minimum=min(numbers) if numbers else None,
                maximum=max(numbers) if numbers else None,
                unit=field_spec.unit if field_spec else None,
                examples=examples,
            )
        )

    # -- required fields, duplicates, coverage ------------------------------
    missing_required: dict[str, list[int]] = {}
    digests: dict[str, list[int]] = {}
    canonical_rows: list[dict[str, Any]] = []
    pan_ids: set[str] = set()
    renamed_pans: list[tuple[str, str, int]] = []
    unusable_pans: list[tuple[str, int]] = []
    timestamps: list[datetime] = []
    naive_count = 0

    for index, row in enumerate(rows, start=1):
        canonical, _ = canonical_row(spec, row)
        canonical_rows.append(canonical)

        for name in spec.required_fields:
            if _is_blank(canonical.get(name)):
                missing_required.setdefault(name, []).append(index)

        raw_pan = canonical.get("pan_id")
        if not _is_blank(raw_pan):
            folded = normalize_pan(str(raw_pan))
            if folded != str(raw_pan):
                renamed_pans.append((str(raw_pan), folded, index))
            if not _PAN_ID_RE.match(folded):
                # An identifier the model will not accept, rather than one it will
                # merely fold. Reported as an error and left alone: rewriting it here
                # would either invent an identifier nobody assigned or silently
                # merge two pans whose names differ only by a character the pattern
                # rejects.
                unusable_pans.append((str(raw_pan), index))
            else:
                pan_ids.add(folded)

        raw_timestamp = canonical.get("timestamp")
        if not _is_blank(raw_timestamp) and isinstance(raw_timestamp, str):
            # A UTC designator is a trailing ``Z`` or an explicit numeric offset.
            # Counting the ones without it is what makes the UTC assumption in
            # :func:`_parse_timestamp` visible instead of silent.
            text = raw_timestamp.strip()
            if not text.endswith("Z") and not _OFFSET.search(text):
                naive_count += 1
            parsed = _parse_timestamp(raw_timestamp)
            if parsed is not None:
                timestamps.append(parsed)

        # Identity for duplicate detection. For a keyed reading the identifier is
        # the content digest, so two rows that hash alike really are the same
        # reading twice. A pan registration has no digest to speak of -- three
        # different pans all hash to the same empty-content id -- so identity there
        # is the pan's own identifier, which is what the unique index uses too.
        if spec.digest_fields:
            identity = content_id(spec, canonical)
        else:
            identity = f"{spec.id_prefix}:{canonical.get('pan_id')}"
        digests.setdefault(identity, []).append(index)

    for name, index_rows in sorted(missing_required.items()):
        audit.findings.append(
            Finding(
                "error",
                "missing_required_field",
                f"{name!r} is required and empty on {len(index_rows)} row(s).",
                rows=tuple(index_rows[:20]),
                column=name,
            )
        )

    exact_duplicates = {d: r for d, r in digests.items() if len(r) > 1}
    if exact_duplicates:
        affected = sorted(r for rows_ in exact_duplicates.values() for r in rows_)
        audit.findings.append(
            Finding(
                "warning",
                "duplicate_records",
                f"{len(exact_duplicates)} record(s) appear more than once with "
                f"identical content, covering {len(affected)} row(s). They carry the "
                "same content-addressed id, so staging keeps one and importing twice "
                "writes one; flagged because duplicated readings usually mean a "
                "double entry or a concatenated export.",
                rows=tuple(affected[:20]),
            )
        )

    if renamed_pans:
        pairs = sorted({(before, after) for before, after, _ in renamed_pans})
        audit.findings.append(
            Finding(
                "warning",
                "pan_id_normalised",
                f"{len(renamed_pans)} row(s) had a pan identifier SaltLens folds to "
                "lower case, e.g. "
                + ", ".join(f"{before!r} -> {after!r}" for before, after in pairs[:6])
                + ". Check the fold did not merge two pans whose names differed only "
                "by case.",
            )
        )

    if unusable_pans:
        offenders = sorted({name for name, _ in unusable_pans})
        audit.findings.append(
            Finding(
                "error",
                "pan_id_unusable",
                f"{len(unusable_pans)} row(s) carry a pan identifier SaltLens cannot "
                f"use: {', '.join(repr(o) for o in offenders[:8])}. The identifier "
                "must match "
                + _PAN_ID_RE.pattern
                + " (letters, digits, dot, underscore, hyphen). Rename it in the "
                "source file: it is not rewritten here, because guessing which pan "
                "'Pan A' meant is a decision a person has to make.",
                rows=tuple(row for _, row in unusable_pans[:20]),
                column="pan_id",
            )
        )

    if spec.name == "pans":
        unset = [
            index
            for index, row in enumerate(rows, start=1)
            if not canonical_rows[index - 1].get("risk_thresholds")
        ]
        if unset:
            audit.findings.append(
                Finding(
                    "warning",
                    "no_risk_thresholds",
                    f"{len(unset)} row(s) declare no risk thresholds. The model "
                    "requires the field but every threshold in it is optional, so "
                    "these pans import with an empty threshold set rather than with "
                    "invented numbers. Anything downstream that compares against a "
                    "threshold will fall back to its own default until the site "
                    "supplies real values.",
                    rows=tuple(unset[:20]),
                    column="risk_thresholds",
                )
            )

    # Only meaningful for the families that are time series. A pan is site
    # registration -- the model has no timestamp field on it at all -- so demanding
    # one would refuse every real pan file for a field that does not exist.
    wants_timestamps = "timestamp" in spec.by_name
    if wants_timestamps and not timestamps:
        audit.findings.append(
            Finding(
                "error",
                "no_readable_timestamps",
                "No row carried a timestamp SaltLens could read, so no record can be "
                "ordered in time and none may be used as a target measurement.",
            )
        )
    elif timestamps:
        audit.first_timestamp = min(timestamps).isoformat()
        audit.last_timestamp = max(timestamps).isoformat()

    if len(pan_ids) < 3 and spec.name != "pans":
        # Not a finding for ``pans``: a registration file holds exactly one row per
        # pan, so "fewer than three" describes the file's whole purpose rather than
        # a shortage. The warning is about evaluating a model on a pan it has not
        # seen, which is a question about readings and outcomes, not about the
        # registry.
        audit.findings.append(
            Finding(
                "warning",
                "few_pans",
                f"{len(pan_ids)} distinct pan(s) in this file. Module 08 needs at "
                "least 3 before a model can be evaluated on a pan it has not seen, "
                "so a dataset from this file alone would be refused at training "
                "time rather than at import time.",
            )
        )

    if naive_count:
        audit.findings.append(
            Finding(
                "warning",
                "assumed_utc",
                f"{naive_count} timestamp(s) carried no timezone and were read as UTC. "
                "If the file records local time, the rows are shifted.",
            )
        )

    audit.distinct_pans = tuple(sorted(pan_ids))
    return audit


def audit_file(
    path: Path, spec: RecordSpec, *, provenance: str | None = None
) -> FileAudit:
    """Audit one source file, returning a report rather than raising on bad data.

    An unreadable file is the one case that produces an unparsable report rather
    than a raised error, because "this file is not a format I can read" is a
    finding about the data directory, and a batch audit of that directory should
    report it and move on to the next file.
    """
    if not path.is_file():
        return FileAudit(
            path=path,
            kind=spec.name,
            collection=spec.collection,
            content_hash="",
            row_count=0,
            provenance=provenance,
            parsable=False,
            findings=[
                Finding("error", "not_a_file", f"{path} is not a readable file.")
            ],
        )
    try:
        rows = read_rows(path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return FileAudit(
            path=path,
            kind=spec.name,
            collection=spec.collection,
            content_hash=file_digest(path) if path.is_file() else "",
            row_count=0,
            provenance=provenance,
            parsable=False,
            findings=[Finding("error", "unreadable", str(exc))],
        )
    return audit_rows(path, spec, rows, provenance=provenance)


#: File suffixes the reader understands.
SOURCE_SUFFIXES = frozenset({".csv", ".json", ".jsonl", ".ndjson"})


def discover(raw_dir: Path, kind: str | None = None) -> tuple[Path, ...]:
    """Every file under ``data/raw`` that may hold records of ``kind``, sorted.

    Two rules keep a mixed directory from producing a wall of irrelevant
    rejections. A subdirectory named after the kind wins, so the recommended layout
    is ``data/raw/observations/…``; and when no such subdirectory exists the files
    are taken as a flat directory for that kind. A pan registration file read as
    weather observations is not a finding about the pan file, it is a mistake in
    how the command was invoked, and reporting one as the other buries the real
    errors.

    ``.gitkeep`` is skipped explicitly: it is a placeholder that keeps an empty
    directory in version control, and reporting it as a source file would mean
    every clean checkout audits as containing one unreadable dataset.
    """
    if not raw_dir.is_dir():
        return ()
    scoped = raw_dir / kind if kind and (raw_dir / kind).is_dir() else raw_dir
    # Recurse only inside a directory dedicated to this kind. Recursing from a flat
    # directory would hand back the other kinds' files, which is the confusion the
    # scoping exists to prevent.
    candidates = scoped.rglob("*") if scoped != raw_dir else scoped.iterdir()
    found = [
        path
        for path in candidates
        if path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in SOURCE_SUFFIXES
    ]
    return tuple(sorted(found))


def render(audit: FileAudit) -> str:
    """A file audit as text, in the same register as the rest of the project."""
    lines = [
        f"file        {audit.path}",
        f"kind        {audit.kind} -> {audit.collection}",
        f"rows        {audit.row_count}",
        f"content     {audit.content_hash or '(unreadable)'}",
        f"provenance  {audit.provenance or '(not declared)'}",
    ]
    if audit.distinct_pans:
        lines.append(f"pans        {len(audit.distinct_pans)}: {', '.join(audit.distinct_pans)}")
    if audit.first_timestamp:
        lines.append(f"span        {audit.first_timestamp} .. {audit.last_timestamp}")

    if audit.columns:
        lines += ["", "columns"]
        for column in audit.columns:
            target = column.canonical_name or "- unmapped -"
            stats = ""
            if column.numeric and column.minimum is not None:
                stats = f"  [{column.minimum:g}, {column.maximum:g}] {column.unit or ''}"
            missing = f"  {column.missing}/{column.present} empty" if column.missing else ""
            lines.append(
                f"  {column.source_name:32} -> {target:32} "
                f"{column.non_null:>5}/{column.present} filled{stats}{missing}"
            )

    if audit.findings:
        lines += ["", "findings"]
        for finding in sorted(
            audit.findings, key=lambda f: (SEVERITIES.index(f.severity), f.code)
        ):
            rows = f" (rows {', '.join(str(r) for r in finding.rows)})" if finding.rows else ""
            lines.append(f"  [{finding.severity}] {finding.code}: {finding.message}{rows}")

    lines += [
        "",
        (
            f"verdict     {'ACCEPTED' if audit.accepted else 'REJECTED'} "
            f"({len(audit.errors)} error(s), {len(audit.warnings)} warning(s))"
        ),
    ]
    return "\n".join(lines)


__all__ = [
    "SEVERITIES",
    "ColumnAudit",
    "FileAudit",
    "Finding",
    "audit_file",
    "audit_rows",
    "discover",
    "read_rows",
    "render",
]
