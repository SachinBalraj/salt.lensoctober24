"""Source-file ingestion - turning a canonical row into a model instance, or a reason why not.

The validators here are the application's own pydantic models. That is the whole
design: a record that survives this module is a record the API would have accepted
for the same input, so a staged file cannot smuggle in a shape the application
would refuse. Nothing is re-implemented here, and nothing is loosened.

**The coercion rule.** A source cell is a string, and a measurement is a number, so
something has to convert between them. That conversion is allowed only in three
cases, and each is a case where the correct value is not in doubt: a declared
numeric field with a parseable number, a timestamp in a format the model reads, and
a closed-vocabulary field whose value is already in the vocabulary. Everything else
is left absent. A cell that says ``high``, ``about 12`` or ``see notes`` in a
salinity column is not a measurement and does not become one; it becomes a finding
and the row is refused.

**Absence is preserved, never filled.** An empty cell becomes ``None`` and stays
``None`` all the way into the staged file. No zero, no mean, no carry-forward of the
previous reading. The models already distinguish a missing measurement from a
measured zero -- ``rainfall_mm`` of ``0`` is a real prediction of no rain, and
``None`` is nobody having said -- and collapsing the two here would destroy the
distinction before any of it reached the dataset builder, which is where it matters.

**No target is ever manufactured.** ``outcomes`` are accepted because a recorded
outcome is a measurement somebody made after an event. Nothing in this module
derives, interpolates or back-fills a label, and there is no code path that could:
the only fields written are the ones the file supplied.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from .audit import _as_number, _is_blank, _parse_timestamp
from .catalog import (
    VALUE_INSTANT,
    VALUE_NUMERIC,
    RecordSpec,
    canonical_row,
    content_id,
    data_source_for,
    id_field,
    normalize_pan,
    provenance_field,
    require_measurement_provenance,
    weather_source_for,
)


@dataclass(frozen=True)
class Rejection:
    """One row that will not become a record, and why."""

    row: int
    reason: str
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"row": self.row, "reason": self.reason, "detail": self.detail}


@dataclass
class ParsedRecords:
    """The outcome of parsing one file: what was accepted, and what was not."""

    spec: RecordSpec
    records: list[Any] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    unmapped: dict[str, int] = field(default_factory=dict)

    @property
    def accepted(self) -> int:
        return len(self.records)

    @property
    def rejected(self) -> int:
        return len(self.rejections)

    @property
    def identifiers(self) -> tuple[str, ...]:
        return tuple(
            str(getattr(record, _id_field(self.spec), "")) for record in self.records
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.spec.name,
            "collection": self.spec.collection,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "rejections": [r.as_dict() for r in self.rejections],
            "unmapped_columns": dict(sorted(self.unmapped.items())),
        }


def _id_field(spec: RecordSpec) -> str:
    """The identifier field this collection's model names."""
    return id_field(spec)


#: Fields that carry a quantity rather than a label, and so must parse as numbers.
_NUMERIC = VALUE_NUMERIC
#: Fields that carry an instant.
_INSTANT = VALUE_INSTANT


def _coerce(
    spec: RecordSpec, canonical: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Convert one canonical row into keyword arguments for the model.

    Returns the kwargs and the reasons a cell could not be read. A cell that cannot
    be read is recorded and left out rather than guessed at, so the caller can name
    the row and column rather than discovering a plausible-looking wrong number
    later.
    """
    by_name = spec.by_name
    values: dict[str, Any] = {}
    problems: list[str] = []

    for name, field_spec in by_name.items():
        raw = canonical.get(name)
        if field_spec.nested_model is not None:
            if isinstance(raw, dict):
                # Pass the assembled object through. Its inner numbers are checked
                # by the nested model's own bounds, which are the authority here --
                # a threshold of 0 for ``target_flow_cm`` is a constraint the field
                # itself states (``gt=0``), so there is no second table to keep in
                # step with it.
                values[name] = {k: v for k, v in raw.items() if v is not None and v != ""}
            else:
                # The model requires the object, but every field inside it is
                # optional, so an absent column becomes an empty threshold set. That
                # is the honest reading -- "no site-specific thresholds recorded" --
                # and inventing values here would put unsourced numbers into every
                # downstream risk threshold. The audit warns so it cannot pass
                # unnoticed.
                values[name] = {}
            continue
        if _is_blank(raw, field_spec.vocabulary):
            continue
        kind = field_spec.value_kind
        if kind == _NUMERIC:
            number = _as_number(raw)
            if number is None:
                problems.append(
                    f"{name}={raw!r} is not a number and was not stored as one"
                )
                continue
            values[name] = number
        elif kind == _INSTANT:
            instant = _parse_timestamp(raw)
            if instant is None:
                problems.append(f"{name}={raw!r} is not a timestamp SaltLens can read")
                continue
            values[name] = instant
        else:
            text = str(raw).strip()
            if field_spec.vocabulary and text not in field_spec.vocabulary:
                problems.append(
                    f"{name}={text!r} is outside {field_spec.vocabulary}"
                )
                continue
            values[name] = text

    if "pan_id" in values:
        values["pan_id"] = normalize_pan(str(values["pan_id"]))

    return values, problems


def _decorate(
    spec: RecordSpec, values: dict[str, Any], provenance: str
) -> dict[str, Any]:
    """Attach the identifier and the provenance fields the model stores.

    The provenance fields are set here and not read from the file, which is the
    point: a source file cannot assert that it is a sensor feed. The declared
    category is the only thing that decides, and it is the researcher's statement
    made at the command line rather than a cell in a spreadsheet.
    """
    if spec.name == "pans":
        # Site registration carries no measurement, so there is no data_source to
        # stamp. The identifier is the pan_id the file supplied and the unique
        # index on salt_pans makes re-registration an update rather than a copy.
        return values
    values[_id_field(spec)] = content_id(spec, values)
    if spec.name == "observations":
        values["data_source"] = data_source_for(provenance)
    elif spec.name == "weather":
        values["source"] = weather_source_for(provenance)
    return values


def parse_record(spec: RecordSpec, row: dict[str, Any], provenance: str) -> Any:
    """Parse one row into a model instance.

    Raises :class:`ValueError` with a reason when the row cannot become a record.
    The reasons are specific because the fix is specific: a required field that is
    empty, a value outside the model's own range, a forecast row with no instant it
    applies to. All of them are statements about this row, never about the file.
    """
    canonical, _ = canonical_row(spec, row)
    values, problems = _coerce(spec, canonical)
    if problems:
        raise ValueError("; ".join(problems))

    for name in spec.required_fields:
        if _is_blank(values.get(name), spec.by_name[name].vocabulary):
            raise ValueError(f"{name} is required and was not supplied")

    decorated = _decorate(spec, values, provenance)
    try:
        return spec.model(**decorated)
    except ValidationError as exc:
        raise ValueError(_render_validation_error(exc)) from exc


def _render_validation_error(exc: ValidationError) -> str:
    """A validation failure as one sentence naming the field and the model.

    Pydantic's own message is good, but it is verbose and it repeats the value of
    every field in the dump. What a researcher needs is which field and why, so the
    report stays readable when a whole file fails the same way.
    """
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error["loc"]) or "(document)"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)


def parse_rows(
    spec: RecordSpec,
    rows: Sequence[dict[str, Any]],
    *,
    provenance: str,
) -> ParsedRecords:
    """Parse every row of a file, collecting rejections rather than stopping at the first.

    A file where one row is malformed is a file with nineteen good rows and one
    question, and stopping at the first error would report the question and hide the
    nineteen. So every row is attempted and every refusal is kept with its row
    number, and the caller decides what to do with the result.
    """
    require_measurement_provenance(provenance)
    outcome = ParsedRecords(spec=spec)

    seen: dict[str, int] = {}
    for index, row in enumerate(rows, start=1):
        _canonical, unmapped = canonical_row(spec, row)
        for column in unmapped:
            outcome.unmapped[column] = outcome.unmapped.get(column, 0) + 1
        try:
            record = parse_record(spec, row, provenance)
        except ValueError as exc:
            outcome.rejections.append(Rejection(row=index, reason=str(exc)))
            continue
        identifier = str(getattr(record, _id_field(spec)))
        if identifier in seen:
            # A content-addressed id can only repeat when the content repeats, so
            # this is a genuine duplicate row rather than a collision. Kept once,
            # because two identical rows are one measurement entered twice.
            outcome.rejections.append(
                Rejection(
                    row=index,
                    reason="duplicate_record",
                    detail=(
                        f"identical to row {seen[identifier]}; same content, so the "
                        "same content-addressed id"
                    ),
                )
            )
            continue
        seen[identifier] = index
        outcome.records.append(record)

    return outcome


def to_rows(spec: RecordSpec, records: Sequence[Any]) -> list[dict[str, Any]]:
    """Render model instances as the canonical dicts a staged file holds.

    Only the fields the source file may supply are emitted. The model's own derived
    and denormalised fields -- ``latest_observation`` on a pan, the three error-term
    blocks on an outcome, which the outcome service computes against linked records
    -- are excluded, because a staged file is the researcher's input and those
    fields are the application's output. Writing them would put a value in the
    file that no measurement produced.
    """
    allowed = {f.name for f in spec.fields}
    origin = provenance_field(spec)
    rows: list[dict[str, Any]] = []
    for record in records:
        dumped = record.model_dump(mode="json")
        row = {name: dumped.get(name) for name in sorted(allowed)}
        row[id_field(spec)] = dumped.get(id_field(spec))
        if origin is not None:
            row[origin] = dumped.get(origin)
        rows.append(row)
    return rows


__all__ = [
    "ParsedRecords",
    "Rejection",
    "parse_record",
    "parse_rows",
    "to_rows",
]
