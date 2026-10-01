# Dataset integration

How a source file becomes a SaltLens record, and what the tooling refuses to do.

Run everything from the repository root:

```
backend/.venv/bin/python -m ml.ingestion <command> [options]
```

---

## 1. What state the data is in

**Atlas is still empty, and still there is no dataset SaltLens can use.**

| Location | Contents |
| --- | --- |
| `data/raw/` | two real third-party datasets, unmodified, plus `.gitkeep` |
| `data/processed/` | `.gitkeep` only |
| `data/exports/` | `.gitkeep` only |
| Atlas, all 18 collections | 0 documents |

`data/raw/` now holds two genuine public datasets acquired after this tooling was
written: NOAA ISD Global Hourly 2024 (weather stations, 61,519 rows) and the
SALTISolutions Rhine-Meuse Delta salinity series (an estuary, 259,913 rows).
Both are **auxiliary** and neither is salt-pan data. Both were run through the
commands below and both were rejected — a weather station is not a salt pan, an
estuary is not a saltern, and neither carries a `pan_id`. Nothing was staged and
nothing was imported. See
[`REAL_DATA_ACQUISITION_REPORT.md`](REAL_DATA_ACQUISITION_REPORT.md) for the
source metadata, licences, hashes, rejected candidates, and the silent
unit-corruption defect that real data exposed in the alias table.

Field-collected data can now be recorded using the header-only templates in
`data/templates/`, following
[`FIELD_COLLECTION_TEMPLATE.md`](FIELD_COLLECTION_TEMPLATE.md).

Two things in the repository look like SaltLens data and are not:

- `frontend/src/lib/demoPans.ts` is demo data, marked as such in the file.
- Backend test fixtures and inline tests are synthetic and never reach the database.

Nothing in this document is a report about a real salt-pan measurement. Atlas
remains empty, and no supervised model can be trained or evaluated until a field
campaign records measured pan state together with a real post-event outcome.

---

## 2. The four commands

| Command | Reads | Writes | Exit 0 means |
| --- | --- | --- | --- |
| `audit` | `data/raw` | nothing | every file passed the audit |
| `validate` | `data/raw` | nothing | every row became a model record |
| `stage` | `data/raw` | `data/processed` | validated rows and manifests written |
| `import` | `data/processed` | the database, **only** with `--commit` | the dry run completed |

Exit codes: `0` success, `1` execution or configuration error, `2` refusal, `3` no
source or staged files found.

```bash
KIND=observations
PROV=FIELD_MEASURED

# 1. read the files and report what is in them
python -m ml.ingestion audit --raw data/raw --kind $KIND --provenance $PROV

# 2. check each row against the real pydantic model
python -m ml.ingestion validate --raw data/raw --kind $KIND --provenance $PROV

# 3. write canonical rows plus a manifest to data/processed
python -m ml.ingestion stage --raw data/raw --kind $KIND --provenance $PROV

# 4. see what import would do -- this writes nothing
python -m ml.ingestion import --processed data/processed

# 5. only once the dry run has been read and agreed with
python -m ml.ingestion import --processed data/processed --commit
```

`import` is dry-run by default. Omitting `--commit` reads the database to count what
would be new and writes nothing.

### Where files go

Put each family in a directory named for it, so `audit` and `import` cannot pick up
the wrong file:

```
data/raw/pans/pan_registry.csv
data/raw/observations/readings.csv
data/raw/weather/forecasts.csv
data/raw/outcomes/harvest_outcomes.json
```

With that layout, `--kind observations` reads `data/raw/observations/` only. Without
it, the files sitting directly in `--raw` are taken as belonging to that kind, and a
pan registry read as a weather file is a mistake in the command, not a finding about
the pan file. `--file` scopes a single file explicitly.

---

## 3. Provenance

`--provenance` is a statement the researcher makes at the command line. A source
file cannot assert it, because the `data_source` / `source` column in a staged file
is written by this tooling, not read from the input.

| Category | Allowed in a measurement collection |
| --- | --- |
| `PUBLIC_MEASURED` | yes |
| `FIELD_MEASURED` | yes |
| `SENSOR_MEASURED` | yes |
| `PHYSICS_DERIVED` | **refused** |
| `SIMULATED` | **refused** |
| `UNKNOWN` | **refused** |

`PHYSICS_DERIVED` and `SIMULATED` records belong in the collections that exist to
hold them (`physics_predictions`, `simulation_runs`), where they are labelled as
what they are. Putting them in `pan_observations` would let a derived salinity enter
the research dataset as though it had been measured.

`UNKNOWN` is refused because an unlabelled measurement is not usable for research:
there is no way to check it later, and by then the source will be forgotten.

---

## 4. What the audit reports

For each file: the content hash, the row count, the distinct pans, the time span,
and per column the canonical field it maps to, how many rows are filled, and the
observed range.

Findings are `error` (the file is rejected) or `warning` (it is kept, and the
reason is recorded). Among them:

| Finding | Severity | Meaning |
| --- | --- | --- |
| `missing_required_field` | error | a field the model requires is empty |
| `non_numeric_measurement` | error | a measurement column holds a value that is not a number |
| `out_of_range` | error | a value is outside the range the model itself enforces |
| `outside_vocabulary` | error | a value is not a member of a closed set such as `harvest_outcome` |
| `pan_id_unusable` | error | an identifier does not match the model's pattern |
| `no_readable_timestamps` | error | no row can be placed in time |
| `unmapped_columns` | warning | a column matched no SaltLens field and is not stored |
| `duplicate_records` | warning | the same record appears more than once |
| `few_pans` | warning | fewer than 3 pans, so Module 08 cannot evaluate on an unseen pan |
| `naive_timestamp` | warning | a timestamp carried no zone; UTC is assumed and recorded |
| `no_risk_thresholds` | warning | a pan declares no thresholds and imports with an empty set |
| `empty_column` | warning | a column is present but empty in every row |

Column names are matched by folding case, spaces, and punctuation, so `Pan ID`,
`pan_id`, and `Pan-ID` are the same column. Aliases are declared in
`ml/ingestion/catalog.py`.

Ranges are read from the pydantic models rather than restated here, including
whether a bound is strict. `SaltPan.area_m2` is `gt=0`, so a zero area is reported
as out of range; an audit that treated the bound as inclusive would pass a value the
model then rejects, which is the one disagreement an audit cannot afford.

### Bounds are never adjusted

A value outside the range is reported for a human to judge and is **not** clipped,
winsorised, or rounded. If the range is wrong for a real site, the model is the thing
to change, and that change is reviewed like any other.

---

## 5. Missing values

A cell counts as absent when it is empty, whitespace, or one of `NA`, `N/A`, `NaN`,
`null`, `none`, `-`, `--`. Absent values are stored as absent, never as zero.

One exception is deliberate. A field with a closed vocabulary is the authority on
its own member values: `HarvestOutcome` contains the member `none`, and a recorded
`none` is a label meaning "nothing was harvested", while a blank cell is an absence.
Reading the word `none` as absent would drop the label the model goes out of its way
to distinguish from `null`, and would do it silently on exactly the rows carrying the
most information.

No value is imputed or interpolated anywhere in this package.

---

## 6. Identifier handling

A reading's identifier is derived from its content:

```
observation_id = "obs-" + sha256(digest_fields)[:24]
```

Consequences, each of which is a property the tests pin down:

- Re-ingesting the same file inserts nothing the second time.
- A row and the same row with an explicit null measurement hash identically.
- A nested object hashes with sorted keys, so a column order change is not a new
  record.
- **A staged file cannot dictate its own identifier.** The `observation_id` column is
  written for whoever reads the file; the digest is what the database is keyed on. An
  edited file therefore cannot collide with an unrelated record.

A pan's identifier is the `pan_id` the file supplied, because that is what the unique
index on `salt_pans` enforces, and re-registering a pan is an update.

---

## 7. Staging and the manifest

`stage` writes a canonical CSV and a `.manifest.json` beside it, recording the source
path, the source content hash, the output content hash, the declared provenance, row
and rejection counts, unmapped columns, distinct pans, and the time span.

Output columns are in a fixed order so two runs over the same input produce
byte-identical files. That is what makes the output digest a provenance record rather
than a checksum of formatting choices.

`import` re-hashes the staged file and refuses to proceed if it no longer matches the
digest recorded at staging time. A file edited between review and import is caught
rather than imported.

`import` also re-checks provenance against the manifest, so the category cannot be
laundered by editing the staged CSV.

---

## 8. The pan risk thresholds

`SaltPan.risk_thresholds` is a nested model and is the one field in the schema that is
not a scalar. It is filled from four flat columns:

```csv
Flood Depth (cm),Dilution Salinity (g/L),Critical Rainfall 24h (mm),Target Flow (cm)
15,268,60,9
```

or from a single JSON column, or from both. Flat columns win on conflict.

The inner numbers are range-checked against the nested model's own constraints. In a
staged file the assembled object is written as JSON, because `import` reads the file
back and Python's `repr` of a dict is not JSON.

When no threshold column is present the pan imports with an **empty** threshold set
and the audit raises `no_risk_thresholds`. The field is required by the model but
every threshold inside it is optional, so refusing the pan would block registering a
site that is otherwise real, and filling the values in would put unsourced numbers
behind every downstream risk threshold. An empty set is the honest reading:
"no site-specific thresholds recorded".

---

## 9. What this package does not do

- It does not write to `data/raw`. Originals are opened read-only.
- It does not train, evaluate, or register a model.
- It does not create `risk_label` or any other target. Those come from recorded
  outcomes through the existing dataset builder.
- It does not read demo or test data.
- It does not create a `risk_label` from a rule; the rule label is a downstream
  product with its own integrity checks.
- It does not connect to Atlas unless `import` is run, and it writes only with
  `--commit`.

## 10. Requirements for a real dataset

Until real files exist, the following are unverified. They are listed as questions
for the data supplier, not as findings.

- Which of the four families exist, and in which format.
- What the columns are actually called, and what units they carry.
- Whether timestamps carry a zone.
- Whether the same pan is recorded under more than one identifier.
- Whether outcomes exist at all, and for how many pans. Without recorded post-event
  salinity there is no target and no supervised model is possible.

When those arrive, run `audit` first. It writes nothing and its output is the honest
first report on the data.
