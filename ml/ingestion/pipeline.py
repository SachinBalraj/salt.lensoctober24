"""Source-file ingestion - the command-line door.

There is exactly one implementation of each step, and it lives in this package and
in the backend's own models. This module decides the order, prints the report and
exits with a code a script can branch on. It adds no logic of its own.

Four commands, in the order they are meant to be run::

    python -m ml.ingestion.pipeline audit  --raw data/raw --kind observations \\
        --provenance FIELD_MEASURED
    python -m ml.ingestion.pipeline validate --raw data/raw --kind observations \\
        --provenance FIELD_MEASURED
    python -m ml.ingestion.pipeline stage   --raw data/raw --kind observations \\
        --provenance FIELD_MEASURED
    python -m ml.ingestion.pipeline import  --processed data/processed/... \\
        --commit

``audit`` reads and describes. ``validate`` builds the records the application
itself would build and reports the ones it refuses. ``stage`` writes what
validated to ``data/processed`` with a manifest, and touches nothing else.
``import`` is the only command that writes to MongoDB, and only with ``--commit``.

**Why ``import`` needs ``--commit``.** Every other command is safe to run twice.
This one writes to a collection that cannot be rolled back from here, in a project
whose research database is shared with a live API. So the dry run is the default,
it prints exactly what it would write, and the flag is required to do it. A
researcher who has staged real measurements should still read the dry run first:
the numbers in it are the only preview of the state of the database.

**Exit codes**, so a script can tell the cases apart::

    0  the command did what was asked
    1  the command could not run (bad arguments, unreadable directory)
    2  refused: the data does not support the step
    3  nothing to do: no source files were found

Exit 2 is the same convention the training pipeline uses for a refusal, and the
same reasoning. A run that correctly finds the data will not do what was asked is a
successful execution of a program that answered "no", and conflating it with a
crash is how a refusal ends up being worked around.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .audit import FileAudit, audit_file, discover, read_rows, render
from .catalog import (
    BACKEND_ENV_FOUND,
    PROVENANCE_REQUIRED_MESSAGE,
    RECORD_SPECS,
    ProvenanceCategory,
    RecordSpec,
    file_digest,
    id_field,
    provenance_field,
    require_measurement_provenance,
    text_digest,
)
from .validate import ParsedRecords, parse_rows, to_rows

#: Process exit codes, named so a caller never has to remember the numbers.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2
EXIT_NO_SOURCES = 3

#: Where sources are read from and validated output is written, relative to the
#: repository root. ``data/README.md`` describes both as git-ignored, which is
#: correct: raw data is the researcher's and never committed, and neither is a
#: derived copy of it.
DEFAULT_RAW = Path("data/raw")
DEFAULT_PROCESSED = Path("data/processed")

#: The name of the manifest written beside every staged file. Fixed rather than
#: derived, so a consumer can find the provenance of a staged file without first
#: having to know the naming scheme.
MANIFEST_NAME = "manifest.json"


class SourceNotFoundError(Exception):
    """No source files matched. Reported as exit 3, not as an error."""


def _resolve_kind(kind: str) -> RecordSpec:
    try:
        return RECORD_SPECS[kind]
    except KeyError:
        raise SystemExit(
            f"unknown kind {kind!r}; expected one of: " + ", ".join(RECORD_SPECS)
        ) from None


def _root(base: Path | None) -> Path:
    """The repository root, when the caller did not pass one.

    Derived from this file's location rather than from the working directory, so
    ``python -m ml.ingestion.pipeline`` run from ``backend/`` still finds
    ``data/raw`` at the top of the repository instead of reporting that no sources
    exist when they do.
    """
    if base is not None:
        return base
    return Path(__file__).resolve().parents[2]


def _stage_name(kind: str, source: Path) -> str:
    return f"{kind}__{source.stem}.csv"


def _provenance_category(value: str | None) -> str | None:
    """Validate a ``--provenance`` value, or return ``None`` to let the audit speak.

    The check is not repeated here. ``require_measurement_provenance`` in the
    catalogue owns that decision and its wording, and having a second copy in the
    argument parser is how a message about refusing simulated data ends up being
    two slightly different sentences depending on which command you ran.
    """
    return value


def _audit_targets(
    raw_dir: Path, kind: str, explicit: Sequence[str] | None
) -> tuple[RecordSpec, list[Path]]:
    spec = _resolve_kind(kind)
    paths = (
        [Path(p) for p in explicit] if explicit else list(discover(raw_dir, spec.name))
    )
    if not paths:
        raise SourceNotFoundError(
            f"no .csv, .json or .jsonl files found in {raw_dir}. Source data goes "
            "in data/raw and is never committed; see data/README.md."
        )
    return spec, paths


def command_audit(args: argparse.Namespace) -> int:
    spec, paths = _audit_targets(args.raw, args.kind, args.paths)
    audits: list[FileAudit] = [
        audit_file(path, spec, provenance=_provenance_category(args.provenance))
        for path in paths
    ]

    if args.json:
        print(json.dumps([a.as_dict() for a in audits], indent=2, default=str))
    else:
        for index, audit in enumerate(audits):
            if index:
                print()
            print(render(audit))
        accepted = sum(1 for a in audits if a.accepted)
        total_rows = sum(a.row_count for a in audits)
        print(
            f"\n{accepted}/{len(audits)} file(s) accepted, {total_rows} row(s) read."
        )

    return EXIT_OK if all(a.accepted for a in audits) else EXIT_REFUSED


def _parse_one(
    spec: RecordSpec, path: Path, provenance: str
) -> tuple[ParsedRecords, FileAudit]:
    """Audit and parse a single file, refusing a file that failed its audit.

    Parsing a file the audit already rejected would produce a partly-valid record
    set from a file that is not what it claims to be -- a simulated file with one
    valid-looking row, say. The audit is the gate; the parse reports the detail.
    """
    audit = audit_file(path, spec, provenance=provenance)
    if not audit.accepted:
        return ParsedRecords(spec=spec), audit
    rows = read_rows(path)
    return parse_rows(spec, rows, provenance=provenance), audit


def command_validate(args: argparse.Namespace) -> int:
    spec, paths = _audit_targets(args.raw, args.kind, args.paths)
    provenance = _provenance_category(args.provenance)

    reports: list[dict[str, Any]] = []
    blocked = False
    for path in paths:
        try:
            parsed, audit = _parse_one(spec, path, provenance or "")
        except ValueError as exc:
            # The provenance refusals raise before any row is touched, which is the
            # correct moment: there is no point parsing 40,000 rows of a file that
            # is not a measurement.
            blocked = True
            reports.append(
                {
                    "file": str(path),
                    "refused": True,
                    "reason": str(exc),
                }
            )
            if not args.json:
                print(f"{path}\n  REFUSED: {exc}\n")
            continue

        blocked = blocked or not audit.accepted
        reports.append({"file": str(path), **parsed.as_dict(), "audit": audit.as_dict()})
        if not args.json:
            print(render(audit))
            print()
            print(
                f"parsed      {parsed.accepted} record(s) accepted, "
                f"{parsed.rejected} rejected"
            )
            for rejection in parsed.rejections[:10]:
                print(f"  row {rejection.row}: {rejection.reason}")
            if len(parsed.rejections) > 10:
                print(f"  ... and {len(parsed.rejections) - 10} more")
            if parsed.unmapped:
                print(
                    "  unmapped columns: "
                    + ", ".join(f"{k} ({v} rows)" for k, v in sorted(parsed.unmapped.items()))
                )
            print()

    if args.json:
        print(json.dumps(reports, indent=2, default=str))
    else:
        total_accepted = sum(r.get("accepted", 0) for r in reports)
        total_rejected = sum(r.get("rejected", 0) for r in reports)
        print(
            f"total       {total_accepted} record(s) would be stored, "
            f"{total_rejected} rejected"
        )
        print(
            f"next        nothing was written. Run 'stage' to write validated rows "
            f"to {args.processed}, then 'import --commit'."
        )

    return EXIT_REFUSED if blocked else EXIT_OK


def _id_field(spec: RecordSpec) -> str:
    return id_field(spec)


def _csv_bytes(spec: RecordSpec, rows: Sequence[dict[str, Any]]) -> bytes:
    """Serialise canonical rows to CSV with stable column order.

    The column order is the specification's own, then the identifier, then the
    field the model records provenance in. That last one is not decoration: a
    staged file is handed to other people, and a file whose rows do not say where
    they came from is a file that has to be trusted rather than checked.

    The order is fixed rather than derived from the rows so that two runs over the
    same input produce byte-identical output, which is what makes the staged file's
    digest a meaningful provenance record rather than a checksum of formatting
    choices.
    """
    columns = [f.name for f in spec.fields] + [_id_field(spec)]
    origin = provenance_field(spec)
    if origin is not None:
        columns.append(origin)
    # A pan's identifier *is* one of its fields, so appending the id column would
    # write ``pan_id`` twice. A duplicated header makes the file ambiguous to read
    # and, worse, means a re-read would have to guess which of the two is the id.
    columns = list(dict.fromkeys(columns))
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {column: _csv_cell(row.get(column)) for column in columns}
        )
    return buffer.getvalue().encode("utf-8")


def _csv_cell(value: Any) -> Any:
    """Render one canonical value for a staged CSV cell.

    A nested object is written as JSON. Python's own ``repr`` would also be a dict
    on one line, but it uses single quotes and is not JSON, so a staged file could
    not be read back -- and re-reading a staged file is exactly what ``import``
    does, which would silently drop the thresholds the file appeared to carry.
    """
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return value


def _id_column(spec: RecordSpec) -> str:
    return id_field(spec)


def command_stage(args: argparse.Namespace) -> int:
    """Write validated rows and a manifest under ``data/processed``.

    The only filesystem writes in this package that are not to a log. Originals in
    ``data/raw`` are opened read-only and never rewritten, and the output directory
    is separate, so a staged file can always be traced back to the source it came
    from by hash.
    """
    spec, paths = _audit_targets(args.raw, args.kind, args.paths)
    provenance = _provenance_category(args.provenance)
    processed = args.processed
    processed.mkdir(parents=True, exist_ok=True)

    written: list[dict[str, Any]] = []
    refused: list[str] = []
    for path in paths:
        try:
            parsed, audit = _parse_one(spec, path, provenance or "")
        except ValueError as exc:
            refused.append(f"{path}: {exc}")
            if not args.json:
                print(f"{path}\n  REFUSED: {exc}")
            continue
        if not audit.accepted or not parsed.records:
            refused.append(f"{path}: {len(audit.errors)} audit error(s)")
            if not args.json:
                print(f"{path}\n  REFUSED: {len(audit.errors)} audit error(s)")
                for finding in audit.errors[:5]:
                    print(f"    [{finding.code}] {finding.message}")
            continue

        rows = to_rows(spec, parsed.records)
        payload = _csv_bytes(spec, rows)
        name = _stage_name(spec.name, path)
        target = processed / name
        target.write_bytes(payload)

        manifest = {
            "kind": spec.name,
            "collection": spec.collection,
            "source_path": str(path),
            "source_content_hash": file_digest(path),
            "provenance": provenance,
            "row_count": len(rows),
            "rejected_count": parsed.rejected,
            "rejections": [r.as_dict() for r in parsed.rejections],
            "unmapped_columns": dict(sorted(parsed.unmapped.items())),
            "distinct_pans": list(audit.distinct_pans),
            "first_timestamp": audit.first_timestamp,
            "last_timestamp": audit.last_timestamp,
            "output_path": name,
            "output_content_hash": text_digest(payload.decode("utf-8")),
            "warnings": [f.as_dict() for f in audit.warnings],
        }
        (processed / f"{name}.{MANIFEST_NAME}").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        written.append(manifest)
        if not args.json:
            print(f"staged      {target}  ({len(rows)} rows, {len(parsed.records)} records)")
            print(f"  source    {file_digest(path)}")
            print(f"  output    {manifest['output_content_hash']}")

    if args.json:
        print(json.dumps({"staged": written, "refused": refused}, indent=2, default=str))
    else:
        print(
            f"\n{len(written)} file(s) staged into {processed}, {len(refused)} refused. "
            "Nothing was written to the database."
        )
        if written:
            print(
                f"next        review the manifests, then 'import {processed} --commit' "
                "to store these records."
            )

    return EXIT_REFUSED if refused else EXIT_OK


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(
            f"{path} has no {MANIFEST_NAME}. Only a staged file can be imported, "
            "because the manifest is where the provenance of each row is recorded."
        )
    return json.loads(path.read_text(encoding="utf-8"))


async def _existing_ids(collection: Any, id_field: str, ids: Sequence[str]) -> set[str]:
    """Which of these identifiers are already stored.

    Batched with ``$in`` rather than one query per row, and in chunks: a file of
    40,000 readings would otherwise be 40,000 round trips, and an unchunked ``$in``
    with 40,000 members is its own problem on the server side.
    """
    found: set[str] = set()
    for start in range(0, len(ids), 500):
        batch = list(ids[start : start + 500])
        async with collection.find({id_field: {"$in": batch}}) as cursor:
            async for document in cursor:
                found.add(document[id_field])
    return found


async def _import_one(
    spec: RecordSpec, manifest_path: Path, *, commit: bool
) -> dict[str, Any]:
    """Import one staged file, or describe what importing it would do."""
    manifest = _read_manifest(manifest_path)
    if manifest["kind"] != spec.name:
        raise SystemExit(
            f"{manifest_path} stages {manifest['kind']!r} records but --kind "
            f"{spec.name!r} was requested"
        )
    provenance = manifest["provenance"]
    # Re-checked at the boundary, not trusted from the file. A manifest is a JSON
    # file on disk and could have been edited since staging, and the refusal has to
    # be re-earned on the way into the database rather than assumed from what the
    # staging run said.
    require_measurement_provenance(provenance)

    csv_path = manifest_path.parent / manifest["output_path"]
    if not csv_path.is_file():
        raise SystemExit(
            f"{csv_path} is missing. A staged file travels with its manifest, and "
            "without it the rows have no recorded provenance to import."
        )
    payload = csv_path.read_bytes()
    current = text_digest(payload.decode("utf-8"))
    if current != manifest["output_content_hash"]:
        raise SystemExit(
            f"{csv_path} no longer matches the digest recorded when it was staged "
            f"({current} now, {manifest['output_content_hash']} then). Re-stage it."
        )

    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))
    parsed = parse_rows(spec, rows, provenance=provenance)

    # The database manager is imported by the package's own import chain, after
    # ``bind_backend_env`` has already pointed the shared settings at the same file
    # the API reads. Re-binding here would be too late and would change nothing.
    from app.db.mongo import db

    health = await db.check_health()
    # HealthResult is a dict, read by key rather than by attribute. Checked before
    # anything is written and named in the message, because "0 records imported" and
    # "the cluster was unreachable" must never look alike from the outside.
    if not health.get("connected"):
        hint = (
            f"backend/.env names a database that did not answer: {health.get('error') or health.get('status')}"
            if BACKEND_ENV_FOUND
            else "no backend/.env was found; set MONGODB_URI and MONGODB_DB_NAME in the environment"
        )
        raise SystemExit(f"cannot reach the database ({hint}). Nothing was written.")

    collection = db.get_collection(spec.collection)
    id_field = _id_field(spec)
    ids = [str(getattr(record, id_field)) for record in parsed.records]
    already = await _existing_ids(collection, id_field, ids)
    new = [
        record
        for record in parsed.records
        if str(getattr(record, id_field)) not in already
    ]

    if not commit:
        return {
            "file": str(manifest_path),
            "collection": spec.collection,
            "would_insert": len(new),
            "already_present": len(parsed.records) - len(new),
            "rejected": parsed.rejected,
            "committed": False,
        }

    if new:
        await collection.insert_many([record.model_dump() for record in new])

    return {
        "file": str(manifest_path),
        "collection": spec.collection,
        "inserted": len(new),
        "already_present": len(parsed.records) - len(new),
        "rejected": parsed.rejected,
        "committed": True,
    }


def command_import(args: argparse.Namespace) -> int:
    # Recursive because a staging directory may be organised one file family per
    # subdirectory, and a run that silently imported only the manifests it happened
    # to find at the top level would report a partial import as a complete one.
    manifests = sorted(args.processed.glob(f"**/*.{MANIFEST_NAME}"))
    if not manifests:
        raise SourceNotFoundError(
            f"no staged manifests found in {args.processed}. Run 'stage' first; an "
            "unstaged file has no recorded provenance and cannot be imported."
        )

    reports: list[dict[str, Any]] = []

    # One event loop for the whole command, not one per file. A Motor client binds
    # itself to the loop that first uses it and dispatches through that loop's
    # executor, so a second ``asyncio.run`` would send the next call to a loop that
    # has already been closed and fail with "Event loop is closed" -- after the first
    # file had already been written. A partial import that aborts on the second file
    # is worse than no import, because the first file is committed and the report
    # never printed.
    async def _run_all() -> list[dict[str, Any]]:
        # Deferred for the same reason as in ``_import_one``: this module's import
        # has to happen after ``bind_backend_env`` has pointed the shared settings
        # at the same file the API reads. The second import returns the same object.
        from app.db.mongo import db

        try:
            return [
                await _import_one(_resolve_kind(kind), manifest_path, commit=args.commit)
                for manifest_path, kind in selected
            ]
        finally:
            await db.close()

    selected: list[tuple[Path, str]] = []
    for manifest_path in manifests:
        kind = json.loads(manifest_path.read_text(encoding="utf-8"))["kind"]
        if args.kind is not None and kind != args.kind:
            continue
        selected.append((manifest_path, kind))

    if not selected:
        raise SourceNotFoundError(
            f"no staged manifest in {args.processed} matched "
            f"--kind {args.kind!r}."
        )

    reports = asyncio.run(_run_all())

    if args.json:
        print(json.dumps(reports, indent=2, default=str))
        return EXIT_OK

    for report in reports:
        if report["committed"]:
            print(
                f"imported    {report['collection']}: {report['inserted']} inserted, "
                f"{report['already_present']} already present, "
                f"{report['rejected']} rejected  ({report['file']})"
            )
        else:
            print(
                f"would write {report['collection']}: {report['would_insert']} new, "
                f"{report['already_present']} already present, "
                f"{report['rejected']} rejected  ({report['file']})"
            )
    if not args.commit:
        print("\ndry run. Nothing was written; re-run with --commit to store these records.")
    return EXIT_OK


def _add_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--raw",
        type=Path,
        default=None,
        help=f"Directory of source files (default: <repo>/{DEFAULT_RAW}).",
    )
    parser.add_argument(
        "--kind",
        required=True,
        choices=sorted(RECORD_SPECS),
        help="What the rows describe.",
    )
    parser.add_argument(
        "--provenance",
        default=None,
        choices=list(ProvenanceCategory.ALL),
        help=(
            "How these rows were obtained. Required for any command that writes. "
            "Only PUBLIC_MEASURED, FIELD_MEASURED and SENSOR_MEASURED can be stored; "
            "PHYSICS_DERIVED, SIMULATED and UNKNOWN are refused."
        ),
    )
    parser.add_argument(
        "--paths",
        nargs="*",
        default=None,
        help="Audit only these files instead of everything in --raw.",
    )
    parser.add_argument(
        "--processed",
        type=Path,
        default=None,
        help=f"Where validated output is written (default: <repo>/{DEFAULT_PROCESSED}).",
    )
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code rather than calling ``exit``."""
    parser = argparse.ArgumentParser(
        prog="ml.ingestion.pipeline",
        description=(
            "Audit, validate, stage and import source measurement files into "
            "SaltLens. Refuses files that are not measurements, and never writes to "
            "the database without --commit."
        ),
        epilog=PROVENANCE_REQUIRED_MESSAGE,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit", help="Describe source files. Writes nothing.")
    _add_source_arguments(audit)
    validate = subparsers.add_parser(
        "validate", help="Parse source files into records. Writes nothing."
    )
    _add_source_arguments(validate)
    stage = subparsers.add_parser(
        "stage", help="Write validated rows and a manifest to data/processed."
    )
    _add_source_arguments(stage)

    importer = subparsers.add_parser(
        "import", help="Store staged records. Needs --commit to write."
    )
    importer.add_argument(
        "--processed",
        type=Path,
        default=None,
        help=f"Staging directory to import from (default: <repo>/{DEFAULT_PROCESSED}).",
    )
    importer.add_argument(
        "--kind",
        default=None,
        choices=sorted(RECORD_SPECS),
        help="Only import staged files of this kind.",
    )
    importer.add_argument(
        "--commit",
        action="store_true",
        help="Actually write. Without this, the command reports what it would do.",
    )
    importer.add_argument("--json", action="store_true", help="Print the report as JSON.")

    arguments = parser.parse_args(argv)
    root = _root(None)
    for name in ("raw", "processed"):
        value = getattr(arguments, name, None)
        if value is None:
            setattr(arguments, name, root / (DEFAULT_RAW if name == "raw" else DEFAULT_PROCESSED))
        elif not value.is_absolute():
            setattr(arguments, name, root / value)

    try:
        if arguments.command == "audit":
            return command_audit(arguments)
        if arguments.command == "validate":
            return command_validate(arguments)
        if arguments.command == "stage":
            return command_stage(arguments)
        if arguments.command == "import":
            return command_import(arguments)
    except SourceNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_NO_SOURCES

    return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
