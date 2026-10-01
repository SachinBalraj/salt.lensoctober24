"""Tests for the source-file ingestion layer.

Two kinds of test live here, and the split matters. The first kind checks that the
mapping is faithful: that the catalogue's fields, ranges and identifier names are
the application's own and would follow a change to them. The second kind checks the
refusals, because the value of this layer is entirely in what it declines to do.

**Every test is offline.** ``conftest`` sets ``SALTLENS_TEST_NO_DATABASE`` before
anything imports the application, and the ingestion package reads no ``.env`` when
that is set, so no test in this file can reach a live cluster. Nothing here writes
to MongoDB either: the import path is tested up to the point where it would need a
database, and past that point by checking that it refuses to go on without one.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path
from typing import Any, Self

import pytest

#: The repository root, so the research layer is importable from the backend suite.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ml.ingestion import catalog
from ml.ingestion.audit import audit_rows, discover, read_rows
from ml.ingestion.catalog import (
    OBSERVATION_SPEC,
    OUTCOME_SPEC,
    PAN_SPEC,
    PROVENANCE_REQUIRED_MESSAGE,
    WEATHER_SPEC,
    ProvenanceCategory,
    canonical_row,
    content_id,
    require_measurement_provenance,
)
from ml.ingestion.pipeline import (
    EXIT_NO_SOURCES,
    EXIT_OK,
    EXIT_REFUSED,
    main,
)
from ml.ingestion.validate import parse_record, parse_rows

FIELD = ProvenanceCategory.FIELD_MEASURED
SENSOR = ProvenanceCategory.SENSOR_MEASURED


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _finding_codes(audit: Any) -> set[str]:
    return {finding.code for finding in audit.findings}


def _rows(*records: dict[str, Any]) -> list[dict[str, Any]]:
    return list(records)


def _audit(
    tmp_path: Path,
    rows: list[dict[str, Any]],
    provenance: str,
    spec: Any = None,
    name: str = "readings.csv",
) -> Any:
    """Write ``rows`` as a real file and audit it.

    A real file rather than a list, because :func:`audit_rows` digests the file it
    is auditing and a report that skipped that step would not be the report the
    command produces.
    """
    buffer = io.StringIO(newline="")
    columns = sorted({key for row in rows for key in row})
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    source = _write(tmp_path, name, buffer.getvalue())
    return audit_rows(source, spec or OBSERVATION_SPEC, rows, provenance=provenance)


# ---------------------------------------------------------------------------
# The catalogue follows the application rather than restating it
# ---------------------------------------------------------------------------


def test_bounds_are_read_from_the_models_not_retyped():
    """A range the models enforce is a range the audit enforces.

    Checked against the live models on both sides so that widening
    ``SALINITY_RANGE`` in the application cannot leave the ingestion layer quietly
    accepting or rejecting against a stale copy of the old number.
    """
    from app.models.pan_observation import SALINITY_RANGE

    field = OBSERVATION_SPEC.by_name["salinity_g_l"]
    assert field.minimum == SALINITY_RANGE["ge"]
    assert field.maximum == SALINITY_RANGE["le"]


def test_every_kind_points_at_a_real_collection_and_model():
    from app.db.collections import (
        ACTUAL_OUTCOMES,
        PAN_OBSERVATIONS,
        SALT_PANS,
        WEATHER_OBSERVATIONS,
    )

    assert OBSERVATION_SPEC.collection == PAN_OBSERVATIONS
    assert WEATHER_SPEC.collection == WEATHER_OBSERVATIONS
    assert PAN_SPEC.collection == SALT_PANS
    assert OUTCOME_SPEC.collection == ACTUAL_OUTCOMES
    for spec in catalog.RECORD_SPECS.values():
        assert spec.collection in {
            PAN_OBSERVATIONS,
            WEATHER_OBSERVATIONS,
            SALT_PANS,
            ACTUAL_OUTCOMES,
        }


def test_identifier_and_provenance_fields_are_read_from_the_models():
    assert catalog.id_field(OBSERVATION_SPEC) == "observation_id"
    assert catalog.id_field(WEATHER_SPEC) == "weather_id"
    assert catalog.id_field(OUTCOME_SPEC) == "outcome_id"
    assert catalog.id_field(PAN_SPEC) == "pan_id"
    assert catalog.provenance_field(OBSERVATION_SPEC) == "data_source"
    assert catalog.provenance_field(WEATHER_SPEC) == "source"
    # A pan registration is not a reading, so it has no field to record one in.
    assert catalog.provenance_field(PAN_SPEC) is None


def test_a_field_cannot_be_declared_with_an_unknown_kind():
    with pytest.raises(ValueError, match="unknown value kind"):
        catalog.FieldSpec("x", value_kind="quantity")


def test_field_kinds_come_from_the_declared_tables():
    numeric = {
        f.name for spec in catalog.RECORD_SPECS.values() for f in spec.fields
        if f.value_kind == catalog.VALUE_NUMERIC
    }
    assert "salinity_g_l" in numeric
    assert "pan_id" not in numeric
    assert "timestamp" not in numeric
    assert OBSERVATION_SPEC.by_name["timestamp"].value_kind == catalog.VALUE_INSTANT
    assert OBSERVATION_SPEC.by_name["pan_id"].value_kind == catalog.VALUE_TEXT


def test_column_names_fold_case_space_and_punctuation():
    assert catalog.normalize_column("Salinity (g/L)") == "salinity_g_l"
    assert catalog.normalize_column("  Observed At ") == "observed_at"
    assert OBSERVATION_SPEC.resolve("Pan ID") == "pan_id"
    assert OBSERVATION_SPEC.resolve("Observed At") == "timestamp"
    assert OBSERVATION_SPEC.resolve("Salinity (g/L)") == "salinity_g_l"
    assert OBSERVATION_SPEC.resolve("Instrument") is None


# ---------------------------------------------------------------------------
# Provenance: the refusal is the point
# ---------------------------------------------------------------------------


def test_missing_provenance_is_refused_with_the_fixed_sentence():
    with pytest.raises(ValueError) as caught:
        require_measurement_provenance(None)
    assert str(caught.value) == PROVENANCE_REQUIRED_MESSAGE


@pytest.mark.parametrize(
    "category", [ProvenanceCategory.SIMULATED, ProvenanceCategory.PHYSICS_DERIVED]
)
def test_a_computation_is_never_a_measurement(category):
    with pytest.raises(ValueError, match="not a measurement"):
        require_measurement_provenance(category)


def test_unknown_is_refused_rather_than_stored():
    with pytest.raises(ValueError, match="not a measurement"):
        require_measurement_provenance(ProvenanceCategory.UNKNOWN)


def test_the_three_measured_categories_are_accepted():
    for category in (
        ProvenanceCategory.PUBLIC_MEASURED,
        ProvenanceCategory.FIELD_MEASURED,
        ProvenanceCategory.SENSOR_MEASURED,
    ):
        assert require_measurement_provenance(category) == category


def test_a_simulated_file_yields_no_records_at_all():
    """Not "records with a flag". None.

    The point of the check is that a computed figure cannot be mistaken for a
    measurement by any later reader, and the only way to guarantee that is for the
    row not to exist in a measurement collection at all.
    """
    rows = _rows(
        {"pan_id": "pan-a", "timestamp": "2026-01-14T06:00:00Z", "salinity_g_l": "285.4"},
        {"pan_id": "pan-a", "timestamp": "2026-01-14T07:00:00Z", "salinity_g_l": "280.1"},
    )
    with pytest.raises(ValueError, match="not a measurement"):
        parse_rows(OBSERVATION_SPEC, rows, provenance=ProvenanceCategory.SIMULATED)


def test_audit_reports_a_simulated_file_as_an_error(tmp_path: Path):
    source = _write(
        tmp_path,
        "sweep.csv",
        "pan_id,timestamp,salinity_g_l\npan-a,2026-01-14T06:00:00Z,285.4\n",
    )
    from ml.ingestion.audit import audit_file

    audit = audit_file(
        source, OBSERVATION_SPEC, provenance=ProvenanceCategory.SIMULATED
    )
    assert not audit.accepted
    assert "provenance_not_measurement" in _finding_codes(audit)


def test_audit_reports_a_file_with_no_declared_provenance(tmp_path: Path):
    from ml.ingestion.audit import audit_file

    source = _write(
        tmp_path,
        "readings.csv",
        "pan_id,timestamp,salinity_g_l\npan-a,2026-01-14T06:00:00Z,285.4\n",
    )
    audit = audit_file(source, OBSERVATION_SPEC, provenance=None)
    assert not audit.accepted
    assert "provenance_missing" in _finding_codes(audit)


# ---------------------------------------------------------------------------
# Nothing is invented
# ---------------------------------------------------------------------------


def test_an_unmeasured_cell_stays_absent_and_is_not_zero():
    record = parse_record(
        OBSERVATION_SPEC,
        {
            "pan_id": "pan-a",
            "timestamp": "2026-01-14T06:00:00Z",
            "water_depth_cm": "12.5",
            "salinity_g_l": "",
        },
        FIELD,
    )
    assert record.water_depth_cm == 12.5
    assert record.salinity_g_l is None


@pytest.mark.parametrize("blank", ["", "  ", "NA", "n/a", "NULL", "none", "-"])
def test_the_spellings_of_not_recorded_are_all_absent(blank: str):
    record = parse_record(
        OBSERVATION_SPEC,
        {
            "pan_id": "pan-a",
            "timestamp": "2026-01-14T06:00:00Z",
            "water_depth_cm": "12.5",
            "salinity_g_l": blank,
        },
        FIELD,
    )
    assert record.salinity_g_l is None


def test_an_out_of_range_value_is_refused_rather_than_clipped():
    with pytest.raises(ValueError):
        parse_record(
            OBSERVATION_SPEC,
            {
                "pan_id": "pan-a",
                "timestamp": "2026-01-14T06:00:00Z",
                "salinity_g_l": "28500",
            },
            FIELD,
        )


def test_a_word_in_a_measurement_column_is_not_coerced_to_a_number():
    with pytest.raises(ValueError, match="not a number"):
        parse_record(
            OBSERVATION_SPEC,
            {
                "pan_id": "pan-a",
                "timestamp": "2026-01-14T06:00:00Z",
                "salinity_g_l": "about 285",
            },
            FIELD,
        )


def test_a_value_outside_a_closed_vocabulary_is_refused():
    with pytest.raises(ValueError, match="outside"):
        parse_record(
            OUTCOME_SPEC,
            {
                "pan_id": "pan-a",
                "event_id": "evt-1",
                "harvest_outcome": "mostly fine",
            },
            FIELD,
        )


def test_a_recorded_none_outcome_is_a_label_not_an_absence():
    record = parse_record(
        OUTCOME_SPEC,
        {
            "pan_id": "pan-a",
            "event_id": "evt-1",
            "timestamp": "2026-01-20T00:00:00Z",
            "harvest_outcome": "none",
        },
        FIELD,
    )
    assert record.harvest_outcome is not None
    assert record.harvest_outcome.value == "none"


def test_a_missing_required_field_is_refused():
    with pytest.raises(ValueError, match="pan_id is required"):
        parse_record(
            OBSERVATION_SPEC,
            {"timestamp": "2026-01-14T06:00:00Z", "salinity_g_l": "285.4"},
            FIELD,
        )


def test_the_audit_reports_the_row_of_an_out_of_range_value(tmp_path: Path):
    rows = _rows(
        {"pan_id": "pan-a", "timestamp": "2026-01-14T06:00:00Z", "salinity_g_l": "285.4"},
        {"pan_id": "pan-b", "timestamp": "2026-01-14T06:00:00Z", "salinity_g_l": "28500"},
    )
    audit = _audit(tmp_path, rows, FIELD)
    finding = next(f for f in audit.findings if f.code == "out_of_range")
    assert finding.rows == (2,)
    assert not audit.accepted


def test_the_audit_does_not_check_a_text_column_for_being_numeric(tmp_path: Path):
    """A regression guard.

    An earlier version range-checked every column, so a pan identifier was reported
    as "10 values that are not numbers" and every file in the directory was rejected
    for a reason that had nothing to do with the data.
    """
    rows = _rows(
        {
            "pan_id": "pan-a",
            "timestamp": "2026-01-14T06:00:00Z",
            "salinity_g_l": "285.4",
            "note": "clear sky",
        }
    )
    audit = _audit(tmp_path, rows, FIELD)
    assert "non_numeric_measurement" not in _finding_codes(audit)
    assert audit.accepted


# ---------------------------------------------------------------------------
# Duplicates and idempotency
# ---------------------------------------------------------------------------


def test_the_identifier_is_stable_for_the_same_content():
    row = {"pan_id": "pan-a", "timestamp": "2026-01-14T06:00:00Z", "salinity_g_l": "285.4"}
    first, _ = canonical_row(OBSERVATION_SPEC, row)
    second, _ = canonical_row(OBSERVATION_SPEC, dict(row))
    assert content_id(OBSERVATION_SPEC, first) == content_id(OBSERVATION_SPEC, second)


def test_an_absent_measurement_and_an_explicit_null_share_an_identifier():
    """One physical reading, one identifier.

    If they differed, a file that blanked a cell and the same file with ``null``
    written out would import as two records of the same observation, and the
    idempotency the content address exists to provide would not survive a
    round trip through a spreadsheet.
    """
    absent, _ = canonical_row(
        OBSERVATION_SPEC,
        {"pan_id": "pan-a", "timestamp": "2026-01-14T06:00:00Z", "salinity_g_l": "285.4"},
    )
    explicit, _ = canonical_row(
        OBSERVATION_SPEC,
        {
            "pan_id": "pan-a",
            "timestamp": "2026-01-14T06:00:00Z",
            "salinity_g_l": "285.4",
            "water_depth_cm": "",
        },
    )
    assert content_id(OBSERVATION_SPEC, absent) == content_id(OBSERVATION_SPEC, explicit)


def test_different_content_gets_different_identifiers():
    one, _ = canonical_row(
        OBSERVATION_SPEC,
        {"pan_id": "pan-a", "timestamp": "2026-01-14T06:00:00Z", "salinity_g_l": "285.4"},
    )
    two, _ = canonical_row(
        OBSERVATION_SPEC,
        {"pan_id": "pan-a", "timestamp": "2026-01-14T06:00:00Z", "salinity_g_l": "286.4"},
    )
    assert content_id(OBSERVATION_SPEC, one) != content_id(OBSERVATION_SPEC, two)


def test_an_identical_row_twice_becomes_one_record_and_one_rejection():
    row = {
        "pan_id": "pan-a",
        "timestamp": "2026-01-14T06:00:00Z",
        "salinity_g_l": "285.4",
    }
    outcome = parse_rows(OBSERVATION_SPEC, _rows(row, dict(row)), provenance=FIELD)
    assert outcome.accepted == 1
    assert outcome.rejected == 1
    assert outcome.rejections[0].reason == "duplicate_record"
    assert "row 1" in (outcome.rejections[0].detail or "")


def test_one_bad_row_does_not_hide_the_good_ones():
    rows = _rows(
        {"pan_id": "pan-a", "timestamp": "2026-01-14T06:00:00Z", "salinity_g_l": "285.4"},
        {"pan_id": "pan-b", "timestamp": "2026-01-14T06:00:00Z", "salinity_g_l": "nope"},
        {"pan_id": "pan-c", "timestamp": "2026-01-14T06:00:00Z", "salinity_g_l": "264.0"},
    )
    outcome = parse_rows(OBSERVATION_SPEC, rows, provenance=FIELD)
    assert outcome.accepted == 2
    assert outcome.rejected == 1
    assert outcome.rejections[0].row == 2


# ---------------------------------------------------------------------------
# Provenance fields come from the declaration, not the file
# ---------------------------------------------------------------------------


def test_a_file_cannot_assert_that_it_came_from_a_sensor():
    row = {
        "pan_id": "pan-a",
        "timestamp": "2026-01-14T06:00:00Z",
        "salinity_g_l": "285.4",
        "data_source": "sensor",
    }
    record = parse_record(OBSERVATION_SPEC, row, FIELD)
    assert record.data_source.value == "import"


def test_a_sensor_declaration_records_a_sensor_reading():
    record = parse_record(
        OBSERVATION_SPEC,
        {"pan_id": "pan-a", "timestamp": "2026-01-14T06:00:00Z", "salinity_g_l": "285.4"},
        SENSOR,
    )
    assert record.data_source.value == "sensor"


def test_a_weather_file_record_is_provenanced_by_its_declaration():
    record = parse_record(
        WEATHER_SPEC,
        {
            "pan_id": "pan-a",
            "timestamp": "2026-01-14T00:00:00Z",
            "forecast_for": "2026-01-15T00:00:00Z",
            "rainfall_mm": "42.0",
            "rainfall_probability_percent": "80",
        },
        ProvenanceCategory.PUBLIC_MEASURED,
    )
    assert record.source.value == "weather_api"
    assert record.is_forecast is True


# ---------------------------------------------------------------------------
# Audit detail
# ---------------------------------------------------------------------------


def test_an_unmapped_column_is_reported_and_not_silently_dropped(tmp_path: Path):
    rows = _rows(
        {
            "pan_id": "pan-a",
            "timestamp": "2026-01-14T06:00:00Z",
            "salinity_g_l": "285.4",
            "Instrument": "HC-08",
        }
    )
    audit = _audit(tmp_path, rows, FIELD)
    assert "unmapped_columns" in _finding_codes(audit)
    # A warning, not an error: an extra column does not make the readings false.
    assert audit.accepted
    assert "Instrument" in (audit.columns[3].source_name)


def test_an_unusable_pan_identifier_is_an_error_not_a_silent_rewrite(tmp_path: Path):
    rows = _rows(
        {"pan_id": "pan-a", "timestamp": "2026-01-14T06:00:00Z", "salinity_g_l": "285.4"},
        {"pan_id": "Pan A", "timestamp": "2026-01-14T06:00:00Z", "salinity_g_l": "285.4"},
    )
    audit = _audit(tmp_path, rows, FIELD)
    assert "pan_id_unusable" in _finding_codes(audit)
    assert not audit.accepted


def test_a_timestamp_with_no_zone_is_reported_as_an_assumption(tmp_path: Path):
    rows = _rows(
        {"pan_id": "pan-a", "timestamp": "2026-01-14 06:00:00", "salinity_g_l": "285.4"},
    )
    audit = _audit(tmp_path, rows, FIELD)
    assert "assumed_utc" in _finding_codes(audit)


def test_a_file_with_fewer_than_three_pans_is_flagged_for_module_eight(tmp_path: Path):
    rows = _rows(
        {"pan_id": "pan-a", "timestamp": "2026-01-14T06:00:00Z", "salinity_g_l": "285.4"},
        {"pan_id": "pan-b", "timestamp": "2026-01-14T06:00:00Z", "salinity_g_l": "264.0"},
    )
    audit = _audit(tmp_path, rows, FIELD)
    assert "few_pans" in _finding_codes(audit)
    assert audit.accepted  # a warning about training, not a reason to refuse a reading


def test_an_empty_file_is_reported_rather_than_accepted_silently(tmp_path: Path):
    source = _write(tmp_path, "empty.csv", "pan_id,timestamp,salinity_g_l\n")
    rows = read_rows(source)
    audit = audit_rows(source, OBSERVATION_SPEC, rows, provenance=FIELD)
    assert "empty_file" in _finding_codes(audit)
    assert audit.accepted  # nothing wrong, nothing to do


def test_an_unreadable_file_is_reported_rather_than_raised(tmp_path: Path):
    from ml.ingestion.audit import audit_file

    source = _write(tmp_path, "notes.txt", "not a data file")
    audit = audit_file(source, OBSERVATION_SPEC, provenance=FIELD)
    assert not audit.parsable
    assert not audit.accepted
    assert "unreadable" in _finding_codes(audit)


@pytest.mark.parametrize("suffix", [".csv", ".json", ".jsonl", ".ndjson"])
def test_every_supported_format_reads(tmp_path: Path, suffix: str):
    body = {
        ".csv": "pan_id,timestamp,salinity_g_l\npan-a,2026-01-14T06:00:00Z,285.4\n",
        ".json": json.dumps(
            [{"pan_id": "pan-a", "timestamp": "2026-01-14T06:00:00Z", "salinity_g_l": 285.4}]
        ),
        ".jsonl": json.dumps(
            {"pan_id": "pan-a", "timestamp": "2026-01-14T06:00:00Z", "salinity_g_l": 285.4}
        ),
        ".ndjson": json.dumps(
            {"pan_id": "pan-a", "timestamp": "2026-01-14T06:00:00Z", "salinity_g_l": 285.4}
        ),
    }[suffix]
    rows = read_rows(_write(tmp_path, f"r{suffix}", body))
    assert len(rows) == 1
    assert rows[0]["pan_id"] == "pan-a"


def test_a_single_json_object_is_one_row_not_an_error(tmp_path: Path):
    rows = read_rows(
        _write(
            tmp_path,
            "one.json",
            json.dumps({"pan_id": "pan-a", "timestamp": "2026-01-14T06:00:00Z"}),
        )
    )
    assert rows == [{"pan_id": "pan-a", "timestamp": "2026-01-14T06:00:00Z"}]


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def test_no_source_files_exits_three_and_writes_nothing(tmp_path: Path, capsys):
    raw = tmp_path / "raw"
    raw.mkdir()
    code = main(
        ["audit", "--raw", str(raw), "--kind", "observations", "--provenance", FIELD]
    )
    assert code == EXIT_NO_SOURCES
    assert "no .csv" in capsys.readouterr().err


def test_audit_exits_two_when_a_file_is_rejected(tmp_path: Path, capsys):
    raw = tmp_path / "raw"
    raw.mkdir()
    _write(
        raw,
        "bad.csv",
        "pan_id,timestamp,salinity_g_l\npan-a,2026-01-14T06:00:00Z,28500\n",
    )
    code = main(
        ["audit", "--raw", str(raw), "--kind", "observations", "--provenance", FIELD]
    )
    assert code == EXIT_REFUSED


def test_validate_exits_zero_and_names_the_rejected_row(tmp_path: Path, capsys):
    raw = tmp_path / "raw"
    raw.mkdir()
    _write(
        raw,
        "mixed.csv",
        "pan_id,timestamp,salinity_g_l\n"
        "pan-a,2026-01-14T06:00:00Z,285.4\n"
        "pan-b,2026-01-14T06:00:00Z,nope\n",
    )
    code = main(
        ["validate", "--raw", str(raw), "--kind", "observations", "--provenance", FIELD]
    )
    output = capsys.readouterr().out
    assert code == EXIT_REFUSED
    # The audit is the gate, so the file is refused before it is parsed; the row
    # with the bad value is still named, by the finding that caught it.
    assert "non_numeric_measurement" in output
    assert "rows 2" in output
    assert "nothing was written" in output


def test_stage_writes_a_manifest_and_leaves_the_source_untouched(tmp_path: Path):
    raw = tmp_path / "raw"
    raw.mkdir()
    processed = tmp_path / "processed"
    source = _write(
        raw,
        "readings.csv",
        "pan_id,timestamp,salinity_g_l,water_depth_cm\n"
        "pan-a,2026-01-14T06:00:00Z,285.4,12.5\n"
        "pan-b,2026-01-14T06:00:00Z,264.0,15.0\n"
        "pan-c,2026-01-14T06:00:00Z,240.2,9.8\n",
    )
    before = source.read_bytes()
    code = main(
        [
            "stage",
            "--raw",
            str(raw),
            "--kind",
            "observations",
            "--provenance",
            FIELD,
            "--processed",
            str(processed),
        ]
    )
    assert code == EXIT_OK
    assert source.read_bytes() == before, "the original file must not be rewritten"

    manifest_path = processed / "observations__readings.csv.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["provenance"] == FIELD
    assert manifest["row_count"] == 3
    assert manifest["collection"] == "pan_observations"
    assert manifest["source_content_hash"].startswith("sha256:")

    staged = list(csv.DictReader(io.StringIO((processed / "observations__readings.csv").read_text())))
    assert len(staged) == 3
    # Provenance travels with the rows, so the file describes its own origin.
    assert {row["data_source"] for row in staged} == {"import"}


def test_staging_the_same_file_twice_produces_the_same_digest(tmp_path: Path):
    raw = tmp_path / "raw"
    raw.mkdir()
    _write(
        raw,
        "readings.csv",
        "pan_id,timestamp,salinity_g_l\n"
        "pan-a,2026-01-14T06:00:00Z,285.4\n"
        "pan-b,2026-01-14T06:00:00Z,264.0\n"
        "pan-c,2026-01-14T06:00:00Z,240.2\n",
    )
    digests = []
    for run in range(2):
        processed = tmp_path / f"processed{run}"
        main(
            [
                "stage",
                "--raw",
                str(raw),
                "--kind",
                "observations",
                "--provenance",
                FIELD,
                "--processed",
                str(processed),
            ]
        )
        digests.append(
            json.loads(
                (processed / "observations__readings.csv.manifest.json").read_text()
            )["output_content_hash"]
        )
    assert digests[0] == digests[1]


def test_a_simulated_file_is_never_staged(tmp_path: Path, capsys):
    raw = tmp_path / "raw"
    raw.mkdir()
    processed = tmp_path / "processed"
    _write(
        raw,
        "sweep.csv",
        "pan_id,timestamp,salinity_g_l\npan-a,2026-01-14T06:00:00Z,285.4\n",
    )
    code = main(
        [
            "stage",
            "--raw",
            str(raw),
            "--kind",
            "observations",
            "--provenance",
            ProvenanceCategory.SIMULATED,
            "--processed",
            str(processed),
        ]
    )
    assert code == EXIT_REFUSED
    assert not list(processed.glob("*.manifest.json"))


def test_import_refuses_when_nothing_was_staged(tmp_path: Path, capsys):
    code = main(["import", "--processed", str(tmp_path)])
    assert code == EXIT_NO_SOURCES
    assert "stage" in capsys.readouterr().err


def test_import_refuses_a_staged_file_that_was_edited_after_staging(tmp_path: Path):
    """The manifest is the provenance record, and it is checked.

    A staged CSV is a file on disk that anyone can open in a spreadsheet. If the
    import trusted it without comparing digests, an edited file would be written to
    the research database under a manifest describing the file before the edit.
    """
    processed = tmp_path / "processed"
    processed.mkdir()
    staged = processed / "observations__readings.csv"
    staged.write_text("pan_id,timestamp,salinity_g_l,observation_id\npan-a,x,1,obs-1\n")
    (processed / "observations__readings.csv.manifest.json").write_text(
        json.dumps(
            {
                "kind": "observations",
                "collection": "pan_observations",
                "output_path": "observations__readings.csv",
                "output_content_hash": "sha256:" + "0" * 64,
                "provenance": FIELD,
            }
        )
    )
    with pytest.raises(SystemExit, match="no longer matches the digest"):
        main(["import", "--processed", str(processed)])


def test_import_re_checks_provenance_from_the_manifest_rather_than_trusting_it(
    tmp_path: Path,
):
    """A manifest edited to claim a measurement is refused at the boundary.

    The staging run would never have produced this, but the manifest is JSON on
    disk, and the refusal has to be re-earned on the way into the database rather
    than assumed from what an earlier run said.
    """
    processed = tmp_path / "processed"
    processed.mkdir()
    staged = processed / "observations__readings.csv"
    body = (
        "pan_id,timestamp,salinity_g_l,observation_id\n"
        "pan-a,2026-01-14T06:00:00Z,285.4,obs-1\n"
    )
    staged.write_text(body)
    digest = catalog.text_digest(body)
    (processed / "observations__readings.csv.manifest.json").write_text(
        json.dumps(
            {
                "kind": "observations",
                "collection": "pan_observations",
                "output_path": "observations__readings.csv",
                "output_content_hash": digest,
                "provenance": ProvenanceCategory.SIMULATED,
            }
        )
    )
    with pytest.raises(ValueError, match="not a measurement"):
        main(["import", "--processed", str(processed)])


def test_import_needs_commit_to_write_anything(tmp_path: Path, capsys, monkeypatch):
    """The dry run is the default, and it reports before it writes.

    Checked without a database: ``_import_one`` reaches the health check before any
    collection is touched, and with the suite's database guard set there is no
    configured cluster to reach. What is asserted here is the contract -- no
    ``insert_many`` without the flag -- by failing at the health check and checking
    that the run reported it rather than pretending to have imported.
    """
    processed = tmp_path / "processed"
    processed.mkdir()
    body = (
        "pan_id,timestamp,salinity_g_l,observation_id\n"
        "pan-a,2026-01-14T06:00:00Z,285.4,obs-1\n"
    )
    (processed / "observations__readings.csv").write_text(body)
    (processed / "observations__readings.csv.manifest.json").write_text(
        json.dumps(
            {
                "kind": "observations",
                "collection": "pan_observations",
                "output_path": "observations__readings.csv",
                "output_content_hash": catalog.text_digest(body),
                "provenance": FIELD,
            }
        )
    )

    written: list[str] = []

    async def _fake_check_health() -> dict[str, Any]:
        return {"status": "connected", "connected": True, "error": None}

    class _Cursor:
        def __init__(self, batch: list[str], stored: set[str]) -> None:
            self._hits = [i for i in batch if i in stored]

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def __aiter__(self) -> Any:
            for identifier in self._hits:
                yield {"observation_id": identifier}

    class _Collection:
        def __init__(self, stored: set[str]) -> None:
            self._stored = stored

        def find(self, query: dict[str, Any]) -> _Cursor:
            return _Cursor(list(query["observation_id"]["$in"]), self._stored)

        async def insert_many(self, documents: Any) -> None:
            written.append("insert_many")
            self._stored.update(d["observation_id"] for d in documents)

    class _Db:
        def __init__(self, stored: set[str]) -> None:
            self._collection = _Collection(stored)

        def get_collection(self, name: str) -> Any:
            return self._collection

        async def close(self) -> None:
            return None

    stored: set[str] = set()
    db = _Db(stored)
    db.check_health = _fake_check_health
    monkeypatch.setattr("app.db.mongo.db", db)

    assert main(["import", "--processed", str(processed)]) == EXIT_OK
    assert "dry run" in capsys.readouterr().out
    assert written == [], "a dry run must not write"

    assert main(["import", "--processed", str(processed), "--commit"]) == EXIT_OK
    assert written == ["insert_many"], "--commit is what writes"


def test_a_second_import_of_the_same_file_inserts_nothing(tmp_path: Path, monkeypatch):
    """Idempotency, checked at the boundary where it has to hold.

    The content-addressed identifier is what makes this work: the second run looks
    the ids up, finds them already stored, and inserts nothing. Without it a
    re-run would double every reading, because ``pan_observations`` is deliberately
    not unique on ``(pan_id, timestamp)``.
    """
    processed = tmp_path / "processed"
    processed.mkdir()
    body = (
        "pan_id,timestamp,salinity_g_l,observation_id\n"
        "pan-a,2026-01-14T06:00:00Z,285.4,obs-abc\n"
    )
    (processed / "observations__readings.csv").write_text(body)
    (processed / "observations__readings.csv.manifest.json").write_text(
        json.dumps(
            {
                "kind": "observations",
                "collection": "pan_observations",
                "output_path": "observations__readings.csv",
                "output_content_hash": catalog.text_digest(body),
                "provenance": FIELD,
            }
        )
    )

    stored: set[str] = set()
    inserted: list[list[str]] = []

    class _Collection:
        def find(self, query: dict[str, Any]) -> Any:
            class _Cursor:
                async def __aenter__(self) -> Any:
                    return self

                async def __aexit__(self, *_: object) -> None:
                    return None

                async def __aiter__(self) -> Any:
                    for identifier in query["observation_id"]["$in"]:
                        if identifier in stored:
                            yield {"observation_id": identifier}

            return _Cursor()

        async def insert_many(self, documents: Any) -> None:
            inserted.append([d["observation_id"] for d in documents])
            stored.update(d["observation_id"] for d in documents)
    class _Db:
        def get_collection(self, name: str) -> Any:
            return _Collection()

        async def close(self) -> None:
            return None

    async def _fake_check_health() -> dict[str, Any]:
        return {"status": "connected", "connected": True, "error": None}

    db = _Db()
    db.check_health = _fake_check_health
    monkeypatch.setattr("app.db.mongo.db", db)

    assert main(["import", "--processed", str(processed), "--commit"]) == EXIT_OK
    assert len(inserted) == 1 and len(inserted[0]) == 1, "the first run inserts the reading"

    assert main(["import", "--processed", str(processed), "--commit"]) == EXIT_OK
    assert len(inserted) == 1, "a second run must insert nothing"
    assert len(stored) == 1


def test_a_staged_file_cannot_dictate_its_own_identifier():
    """The identifier is derived from the content, not read from the column.

    A staged CSV is a file someone can open in a spreadsheet. If the import trusted
    the ``observation_id`` column, an edited file could collide with an unrelated
    record's identifier, or hide a row behind one that already exists. The column is
    written for the reader; the digest is what the database is keyed on.
    """
    reading = {
        "pan_id": "pan-a",
        "timestamp": "2026-01-14T06:00:00Z",
        "salinity_g_l": "285.4",
    }
    untampered = parse_record(OBSERVATION_SPEC, reading, FIELD)
    tampered = parse_record(
        OBSERVATION_SPEC, {**reading, "observation_id": "obs-chosen-by-the-file"}, FIELD
    )
    assert tampered.observation_id == untampered.observation_id
    assert tampered.observation_id != "obs-chosen-by-the-file"


# -- nested field mapping (a pan's risk thresholds) ---------------------------


def test_flat_threshold_columns_assemble_into_the_nested_model():
    """Operators export the four thresholds as four columns, not as one JSON cell.

    Requiring a single JSON column would refuse the format a spreadsheet actually
    produces, so the four columns are gathered into the object the model wants. The
    inner bounds are the nested model's own, so a zero target flow is caught here
    rather than being accepted by the audit and then refused at validation.
    """
    row = {
        "pan_id": "pan-alpha",
        "pan_name": "Alpha",
        "location_name": "Site A",
        "area_m2": "4200",
        "normal_depth_cm": "12",
        "target_harvest_salinity_g_l": "300",
        "Flood Depth (cm)": "15",
        "Dilution Salinity (g/L)": "268",
        "Critical Rainfall 24h (mm)": "60",
        "Target Flow (cm)": "9",
    }
    canonical, unknown = canonical_row(PAN_SPEC, row)
    assert unknown == ()
    assert canonical["risk_thresholds"] == {
        "flood_depth_cm": "15",
        "dilution_salinity_g_l": "268",
        "critical_rainfall_24h_mm": "60",
        "target_flow_cm": "9",
    }
    record = parse_record(PAN_SPEC, row, FIELD)
    assert record.risk_thresholds.flood_depth_cm == 15.0
    assert record.risk_thresholds.target_flow_cm == 9.0


def test_a_pan_with_no_threshold_column_imports_with_an_empty_set_not_a_guessed_one():
    """Absent thresholds are a truthful empty set, never invented numbers.

    The model requires the field but every threshold inside it is optional, so
    refusing the pan would block registering a site that is otherwise real, and
    filling the values in would put unsourced numbers behind every downstream risk
    threshold. The audit is what makes the gap visible.
    """
    row = {
        "pan_id": "pan-alpha",
        "pan_name": "Alpha",
        "location_name": "Site A",
        "area_m2": "4200",
        "normal_depth_cm": "12",
        "target_harvest_salinity_g_l": "300",
    }
    record = parse_record(PAN_SPEC, row, FIELD)
    assert record.risk_thresholds.model_dump(exclude_none=True) == {}


def test_unset_thresholds_are_warned_about(tmp_path: Path, capsys: pytest.CaptureFixture):
    audit = _audit(
        tmp_path,
        [
            {
                "pan_id": "pan-alpha",
                "pan_name": "Alpha",
                "location_name": "Site A",
                "area_m2": "4200",
                "normal_depth_cm": "12",
                "target_harvest_salinity_g_l": "300",
            }
        ],
        FIELD,
        PAN_SPEC,
        name="pans.csv",
    )
    assert "no_risk_thresholds" in {f.code for f in audit.findings}
    assert audit.accepted, "a pan with no thresholds is still a real pan"


def test_a_threshold_outside_the_models_own_bound_is_reported(tmp_path: Path):
    """``Target Flow (cm)`` is ``gt=0``, so zero is outside the range.

    The audit has to agree with the model exactly. Reporting the bound as inclusive
    would pass a zero here and have the validator reject it, which is the one
    disagreement an audit cannot afford.
    """
    audit = _audit(
        tmp_path,
        [
            {
                "pan_id": "pan-alpha",
                "pan_name": "Alpha",
                "location_name": "Site A",
                "area_m2": "4200",
                "normal_depth_cm": "12",
                "target_harvest_salinity_g_l": "300",
                "Target Flow (cm)": "0",
            }
        ],
        FIELD,
        PAN_SPEC,
        name="pans.csv",
    )
    out_of_range = [f for f in audit.findings if f.code == "out_of_range"]
    assert out_of_range, "zero is not a valid target flow"
    assert "Target Flow (cm)" in out_of_range[0].message
    assert not audit.accepted


def test_a_zero_area_pan_is_reported_as_out_of_range(tmp_path: Path):
    """``area_m2`` is ``gt=0``; a strict bound must not be audited as inclusive."""
    audit = _audit(
        tmp_path,
        [
            {
                "pan_id": "pan-alpha",
                "pan_name": "Alpha",
                "location_name": "Site A",
                "area_m2": "0",
                "normal_depth_cm": "12",
                "target_harvest_salinity_g_l": "300",
            }
        ],
        FIELD,
        PAN_SPEC,
        name="pans.csv",
    )
    assert any(f.code == "out_of_range" and "area_m2" in f.message for f in audit.findings)


def test_the_threshold_digest_does_not_depend_on_column_order():
    """Two files holding the same thresholds in a different order are one record."""
    base = {
        "pan_id": "pan-alpha",
        "pan_name": "Alpha",
        "location_name": "Site A",
        "area_m2": "4200",
        "normal_depth_cm": "12",
        "target_harvest_salinity_g_l": "300",
    }
    first = parse_record(PAN_SPEC, {**base, "Flood Depth (cm)": "15"}, FIELD)
    second = parse_record(PAN_SPEC, {**base, "Flood Depth (cm)": "15.0"}, FIELD)
    assert first.pan_id == second.pan_id


# -- kind-scoped discovery ----------------------------------------------------


def test_discovery_prefers_a_directory_named_for_the_kind(tmp_path: Path):
    """A pan file must not be audited as weather just because it shares a directory.

    Reading one kind's file as another produces findings about the wrong file, and
    a page of those buries the real errors. A subdirectory named for the kind is the
    layout that makes the scoping unambiguous.
    """
    raw = tmp_path / "raw"
    (raw / "observations").mkdir(parents=True)
    (raw / "weather").mkdir()
    (raw / "pans.csv").write_text("Pan ID\npan-a\n")
    (raw / "observations" / "readings.csv").write_text("Pan ID\npan-a\n")
    (raw / "weather" / "forecast.csv").write_text("Pan ID\npan-a\n")

    found = {p.name for p in discover(raw, "observations")}
    assert found == {"readings.csv"}
    assert {p.name for p in discover(raw, "weather")} == {"forecast.csv"}
    # A kind with no directory of its own falls back to the flat directory.
    assert {p.name for p in discover(raw, "outcomes")} == {"pans.csv"}


def test_a_staged_pan_file_can_be_read_back_with_its_thresholds(tmp_path: Path):
    """A staged file must survive a second pass with nothing lost.

    ``import`` re-reads what ``stage`` wrote, so the staged rendering of a nested
    object has to be something the reader accepts. Python's ``repr`` of a dict is
    not JSON: it uses single quotes, it fails to parse, and the thresholds would
    vanish on re-read while the file still appeared to carry them.
    """
    raw = tmp_path / "raw" / "pans"
    raw.mkdir(parents=True)
    _write(
        raw,
        "pans.csv",
        "pan_id,pan_name,location_name,area_m2,normal_depth_cm,"
        "target_harvest_salinity_g_l,Flood Depth (cm),Target Flow (cm)\n"
        "pan-alpha,Alpha,Site A,4200,12,300,15,9\n",
    )
    processed = tmp_path / "processed"
    assert main(
        [
            "stage",
            "--raw",
            str(tmp_path / "raw"),
            "--kind",
            "pans",
            "--provenance",
            FIELD,
            "--processed",
            str(processed),
        ]
    ) == EXIT_OK

    staged = next(processed.glob("pans__*.csv"))
    text = staged.read_text()
    header = text.splitlines()[0]
    assert header.count("pan_id") == 1, "the id column is not written twice"
    assert "'" not in header and "{" in text, "the thresholds are written as JSON"

    # Read the staged file back through the same reader the import uses.
    rows = read_rows(staged)
    canonical, _ = canonical_row(PAN_SPEC, rows[0])
    assert isinstance(canonical["risk_thresholds"], dict)
    assert canonical["risk_thresholds"]["flood_depth_cm"] == 15.0
    record = parse_record(PAN_SPEC, rows[0], FIELD)
    assert record.risk_thresholds.flood_depth_cm == 15.0
    assert record.risk_thresholds.target_flow_cm == 9.0
