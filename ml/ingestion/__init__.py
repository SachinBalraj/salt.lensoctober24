"""Source-file ingestion - getting a researcher's data file into SaltLens, honestly.

This package is the answer to a gap that Module 07 did not cover. Everything
downstream of a measurement already exists: the feature catalogue, the target
catalogue, the leakage audit, the dataset versioning, the sufficiency gate. What
did not exist was the step before any of it, where a spreadsheet becomes records.

**The rule the whole package is built around.** A number that reaches
``pan_observations``, ``weather_observations`` or ``actual_outcomes`` is a claim
that somebody measured it. So a row that is not a measurement never gets there:
its provenance is declared on the command line, checked against the destination,
and a ``SIMULATED`` or ``PHYSICS_DERIVED`` file is refused outright rather than
imported with a flag on it. There is no mode in which the pipeline generates a
label, fills a blank, interpolates a gap or clips an outlier into range.

**What it will not do, stated once.** It will not infer a unit, convert one, or
guess which of two columns named ``salinity`` is the real one. Unit ambiguity is
reported as a finding for a human to resolve, because a pipeline that guesses
units is a pipeline that will eventually be wrong silently, and a salinity in the
wrong unit is a plausible-looking number.

The four steps, each runnable on its own::

    audit      read the files and describe what is in them
    validate   build the records the application would build, refuse the rest
    stage      write validated rows and a manifest to data/processed
    import     store staged records, and only with --commit

See ``ml/ingestion/pipeline.py`` for the command line and ``docs/DATASET_INTEGRATION.md``
for the audit and the reasoning behind each field.
"""

from __future__ import annotations

from .audit import FileAudit, Finding, audit_file, discover, read_rows, render
from .catalog import (
    OBSERVATION_SPEC,
    OUTCOME_SPEC,
    PAN_SPEC,
    PROVENANCE_REQUIRED_MESSAGE,
    RECORD_SPECS,
    WEATHER_SPEC,
    ProvenanceCategory,
    RecordSpec,
    require_measurement_provenance,
)
from .validate import ParsedRecords, Rejection, parse_record, parse_rows, to_rows

__all__ = [
    "OBSERVATION_SPEC",
    "OUTCOME_SPEC",
    "PAN_SPEC",
    "PROVENANCE_REQUIRED_MESSAGE",
    "RECORD_SPECS",
    "WEATHER_SPEC",
    "FileAudit",
    "Finding",
    "ParsedRecords",
    "ProvenanceCategory",
    "RecordSpec",
    "Rejection",
    "audit_file",
    "discover",
    "parse_record",
    "parse_rows",
    "read_rows",
    "render",
    "require_measurement_provenance",
    "to_rows",
]
