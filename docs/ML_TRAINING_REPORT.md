# Stage 6 — ML Model Training and Evaluation

**Verdict: `ML NOT READY`. No model was fitted, no metric was produced, no
artifact was written.** Stage 5 established that no compatible public data
exists; this stage confirmed it at the training layer, found and fixed a real
gap in how the pipeline decides whether a score is evidence, and repaired a
broken command-line door onto the training run.

Everything below is measured against the running system. No number here is
projected, and no threshold was chosen to make the gate pass.

---

## 1. Readiness

`ML NOT READY`, for the same two independent reasons Stage 5 reported, now
confirmed against the database rather than against downloaded files.

| Requirement (from the training layer's own gate) | Status |
|---|---|
| Labelled target | **Absent** — the one outcome has `harvest_outcome: null` |
| Genuine field data | **Absent** — 2 observations, both `data_source=demonstration` |
| ≥ 3 independent pans | **Absent** — `pans: 1`, and it is `pan-demo-01` |
| Leakage-safe split | Not constructible — nothing to split |
| Comparable forecast/actual pairs | **Absent** — the outcome has no measurement |

Measured live:

```
pans            1     (pan-demo-01)
observations    2     (both data_source=demonstration)
weather         2
outcomes        1     (is_demonstration=true, harvest_outcome=null)
models          0
experiments     0
artifacts       0     ml/models/ is empty
```

The decisive fact is not the count of outcomes, it is their content. The single
outcome has `harvest_outcome: null`, so it is not a weak label — it is **no
label**, and its `demonstration_sources` are
`['pre_event_observation_data_source', 'weather_forecast_source']`, meaning the
pre-event state and the forecast behind it were both typed to demonstrate the
workflow. There is nothing to fit and nothing to fit it to.

---

## 2. Sources and versions

Nothing was trained, so there is no model source. The inputs that would have
fed a run:

| Input | Value |
|---|---|
| Stored datasets | 2 — `ds-acec164dd3824d628c7deb2babe5cdd2` (v1, `demo-isolation-check`), `ds-d1b073f450b3492daa8b5fce9bc56b48` (v2, `demo-isolation-wide`) |
| Dataset targets | both `post_rain_salinity_g_l`, `kind: regression` |
| Dataset `provenance` | `None` on both |
| Observation sources | `demonstration` (2 of 2) |
| Outcome provenance | `is_demonstration: true`, sources as above |

Library versions used by the pipeline and recorded on every model version:
scikit-learn, numpy, pandas, scipy, joblib — captured per run via
`library_versions()` and not transcribed here, because quoting them from the
interpreter rather than from a recorded run would be the same mistake this
report is about.

**Both stored datasets target a regression.** Module 08 fits classifiers only,
so it refuses both by target name before it ever counts a row. That is a correct
refusal, recorded as a finding in §10 rather than treated as the readiness
answer — the readiness answer is the missing labels, which a classifier could not
have used either.

---

## 3. Target definition

Not defined, and deliberately not manufactured. Stage 6 instruction 3 requires an
operational-risk target built from *documented observed outcomes and justified
criteria*. There are no observed outcomes, so any criteria would be invented.

The pipeline already offers three supported classification targets —
`harvest_outcome`, `operational_risk`, `post_rain_salinity_band` — and each
carries the machinery to refuse an unjustified one:

- `post_rain_salinity_band` requires `bin_edges`. Thresholds for those bins would
  have to be chosen now, from no observed distribution of post-rain salinity.
  Choosing them would make the bins, and therefore the label, a product of
  assumption.
- `operational_risk` requires a stored `RiskDefinition` carrying its own
  specification hash, and refuses to fit if the builder's rows come back
  unlabelled (`services/ml.py`, `_unlabelled_risk_report`). The rule's criteria
  must be stated before they can fire; stating them from nothing is the same
  fabrication one level down.
- `harvest_outcome` requires an observed harvest result. Zero exist.

None was defined. The target column of every report is therefore **not
defined**, and that is the finding.

---

## 4. Sample count

| Quantity | Count |
|---|---|
| Labelled rows available | **0** |
| Rows with a label at all | 0 — the one outcome's `harvest_outcome` is null |
| Genuine measured pan observations | **0** |
| Distinct pans | 1, and it is the demonstration pan |
| Distinct events with linked pre/post outcome | 0 |

Minimums the training layer enforces, for scale: 80 labelled rows total, 20 in
the smaller split, 5 per class, 3 per class in the training split, 3 distinct
pans, 2 features. The available count is not near these margins; it is zero
against all of them.

---

## 5. Split

Not performed. `make_split` was never reached: the run refuses at the
dataset-level gate before a split is attempted, which is the designed order —
a dataset with no labelled rows cannot be split into two usable halves, and the
error should name the data rather than the split.

Available strategies, unchanged and unused: `chronological` (default),
`grouped_by_pan`, `random`, `per_pan_chronological`. Their leakage properties
were reviewed and are sound; the property that matters most for this stage is
re-checked in §8.

---

## 6. Trained models

**None.** All four agreed candidates are implemented and were exercised by the
test suite against labelled fixtures:

- `logistic_regression`
- `decision_tree`
- `random_forest`
- `gradient_boosting`

Random Forest remains fitted and scored on every run but never selected
automatically; selecting it requires an explicit `allow_random_forest`, recorded
on the model version. Selection rule: highest macro F1 among automatically
selectable candidates.

None of the four was fitted to real data in this stage, because there is none.

---

## 7. Actual metrics

**None. There is no accuracy, precision, recall, F1, confusion matrix,
ROC-AUC, PR-AUC, class distribution or calibration figure for any model in this
system, and none should be quoted.** `models: 0`, `experiments: 0`,
`ml/models/` empty.

This is not a gap in reporting; it is the finding. Stage 5 found 486,553 public
rows, none importable because `OBSERVATION_SPEC` and `WEATHER_SPEC` both require
`pan_id` and neither has a latitude/longitude field — SaltLens is pan-scoped, and
importing a station or a marsh would mean minting a `SaltPan` that does not
exist. Stage 6 closes the other half of the same gap: there is no label source
either. Admitting the first without the second would produce a model with
features and no target.

---

## 8. Model selection

No selection occurred. The selection machinery is unchanged and documented: it
compares candidates on held-out macro F1, requires every candidate to be fitted
and scored so the comparison is not between a tuned winner and untried runners,
and refuses to let Random Forest win automatically. None of that ran.

---

## 9. Artifacts

**None written.** `ml/models/` contains only its `.gitkeep`. The refusal path
leaves nothing behind by design: the sufficiency gate runs before any estimator
is constructed and before any preprocessing statistic is computed, so an
insufficient dataset cannot leave a partial artifact for a later step to pick up.
Verified after every run in this stage.

---

## 10. Findings — defects found and fixed

Stage 6's instruction 2 asks for the training pipeline to be improved. Four
defects were found. The first is the important one.

### 10.1 The pipeline had no provenance gate — the significant gap

`app/ml/sufficiency.py` checks volume, class counts, pan spread and feature
count. It checked nothing about whether the data is a *measurement*. Before this
stage, the demonstration pan and demonstration outcome in the development
database were a trainable dataset: they clear every sufficiency count, and a
model fitted to them would report a well-formed held-out F1 that describes four
typed rows. Nothing downstream could detect this, because **a metric does not
carry its own provenance.**

Fixed by adding `backend/app/ml/provenance.py`:

- `EvidenceClass` — `genuine` / `demonstration` / `mixed` / `unknown`.
- `classify_evidence(rows, demonstration_targets=…)` — classifies from the
  provenance each `DatasetRow` already carries (`observation_data_source`,
  `weather_source`, `target_source_id`), reading the `is_demonstration` flag off
  the outcome itself rather than re-deriving the rule.
- Severity ordering: one demonstrative row among genuine ones yields `mixed`,
  not `genuine`. Averaging away the rows that invalidate the claim is the failure
  being prevented.
- **Fails closed.** A row with no observable source is `unknown`, and a model
  version recording no evidence at all is `unknown`. Defaulting either to
  `genuine` would let an old number quietly become a field claim.

Wired into three places:

| Location | Effect |
|---|---|
| `services/ml.py::train` | Records `parameters["evidence"]` on every model version |
| `services/evaluation.py::_check_evaluable` | Refuses the evaluation with the counts |
| `api/routers/ml.py` | `/api/ml/` reports `evidential_models: 0` |

**Training is deliberately not blocked.** Fitting a model to fixture data is how
this pipeline is tested; forbidding it would mean the training code could only
run against data that does not exist yet. What is blocked is the *claim*: a
model fitted to demonstration data is a legitimate thing to have, and one whose
score is reported as real-world performance is not. This distinction is what let
all 28 existing `test_ml_api.py` tests keep passing unchanged — they exercise the
maths, which is still permitted.

Two bugs were found in this new code while testing it, both worth recording:

- The first classifier inferred the class by searching its own prose for the
  word `"demonstration"`. The message for a typed observation says
  *"demonstrate"*, so **every demonstration row was silently filed as
  untraceable instead**. Classification is now driven by the value; the strings
  are for the reader.
- `demonstration_target_ids` read the flag with `getattr` only, so a plain
  document returned `None` and every outcome looked genuine — the exact failure
  the module exists to prevent, reproduced by its own helper.

### 10.2 Leakage was checked only at build time

`dataset_builder.py` refuses a feature whose `leakage_class` is not knowable at
the anchor, so a freshly built manifest is clean. But `train()` then trusted
that manifest. A manifest that was hand-edited, restored from a backup, or
written by an older builder could carry a post-event measurement into an
estimator, which trains cleanly, scores well, and records nothing wrong — the
score is the label leaking through a second column.

Added `MLService._check_leakage(dataset)`, refusing before any estimator exists
with `LeakageDetectedError`. It also deliberately does **not** refuse a feature
the manifest does not describe: guessing there would refuse datasets for a
reason the reader cannot act on.

### 10.3 The research CLI could not train at all

`ml/training/pipeline.py::_service()` passed a `MongoDatabase` manager where a
database was required, so every invocation died with
`'MongoDatabase' object is not subscriptable` — a traceback pointing at the
repository, roughly forty lines from the actual mistake. **Module 08's
command-line door had never worked.** Three further defects sat behind it:

1. Passing `app.api.deps.get_db`, the same provider every router uses.
2. Opening the connection — a standalone process never runs the application's
   startup event, so nothing is connected yet.
3. **Connect and train on one event loop.** A Motor client binds to the loop
   that created it; connecting in one `asyncio.run` and training in another
   strands the connection on a closed loop and every read then fails against a
   database that is genuinely reachable.
4. Closing the client on the same loop. A CLI that keeps a Mongo client open
   does not exit when the work is done — it hangs *after* printing a correct
   refusal, which reads as a slow query.

Refusals other than insufficiency are now reported rather than raised:
`UnsupportedTargetError`, `DatasetDriftError` and `LeakageDetectedError` print
their message and exit `3` (insufficient data stays `2`). Each needs a different
fix, so they must not share a code.

The door now works end to end and reports the database's real state:

```
$ python -m ml.training.pipeline train ds-acec164dd3824d628c7deb2babe5cdd2
Target 'post_rain_salinity_g_l' is not trainable here. This module fits
classification targets only (harvest_outcome, operational_risk,
post_rain_salinity_band) …                       exit 3
```

### 10.4 A tempting rule that was wrong

An intermediate version of the evidence gate treated *absent* `weather_source` on
a weather-reading row as untraceable. It fired on 120 of 120 rows of legitimate
fixture data and would have refused nearly every real dataset — turning an
evidence gate into a completeness check, which is a different question with a
different fix. Reverted: a demonstration marker is an explicit positive signal,
while an unfilled optional field is silence, not a typed row.

---

## 11. Tests

```
backend    1083 passed   (1066 before + 17 new), 69 warnings, 103s
frontend    39 files, 816 passed; tsc --noEmit and eslint clean
ruff        clean on every file touched in this stage
```

17 new tests in `backend/tests/test_ml_provenance.py`, covering:

- measured rows are the only evidential ones;
- a demonstration **forecast** disqualifies a row whose observation is genuine —
  the case a gate looking only at observations would pass;
- a label from a demonstration outcome disqualifies a measured observation;
- one demonstration row among genuine ones is `mixed`, with counts;
- a row with no provenance is `untraceable`, not assumed genuine;
- an absent weather source is not treated as a typed one;
- the **empty** set is `unknown`, so `all()` over nothing cannot read as true;
- a model version recording no provenance **fails closed** (four malformed
  shapes);
- a genuine verdict survives the `as_dict` → `verdict_from_parameters` round trip;
- `demonstration_target_ids` reads the outcome's own flag from a document;
- a `future` feature is refused before an estimator exists, while `as_of` and
  `static` pass, and an undescribed feature is left to the builder.

**No existing test was weakened.** All 41 `test_ml_pipeline.py`, 28
`test_ml_api.py`, 14 `test_evaluation_absence.py` and 21 `test_experiment_api.py`
tests pass untouched. That was the constraint that shaped the design: the fix
had to *mark* demonstration-trained models rather than refuse to fit them,
because `test_ml_api.py` legitimately trains on fixture data to test the
pipeline.

---

## 12. Limitations

- **No model, no metric, no artifact.** Nothing in this stage is a performance
  claim, and the Stage 5 report's `ML NOT READY` is unchanged.
- **Both blockers stand independently.** No labels (zero `harvest_outcome` values
  anywhere public or local) and no compatible observations (486,553 rows, all
  outside a schema that requires `pan_id` and has no coordinates). Either alone
  is sufficient.
- **The demonstration data is still in the database.** The gate stops it being
  *reported* as performance; it does not remove it, and it should stay until the
  field campaign produces something to compare against.
- **The sufficiency thresholds were not revisited.** 80 labelled rows, 20 per
  split, 5 per class and 3 pans are the project's existing, documented
  constants. Lowering them to reach readiness would convert a refusal into a
  number without adding evidence, so they were left alone. Their adequacy is a
  methodological question for when real counts exist.
- **One leakage axis is not covered.** `leakage_class` is declared in the
  catalogue and enforced at build and now at train time. A *feature that is
  legitimately as-of but correlated with the label by construction* — a derived
  physics prediction, for instance — is not detectable by any of these checks.
  That is a property of the feature catalogue, not something a gate can fix.
- **`evidential_models: 0` is a static statement**, not a computed count. There
  are no models, so the two agree; a live count would need a query per status
  call and is left until something is registered.
- **No calibration evidence.** Even had a model been fitted, calibration would
  have needed a reliability assessment against held-out probabilities. Nothing
  was fitted, so no such assessment exists to report.

---

## 13. Next action

1. **Run the field campaign.** `data/templates/` plus
   [`FIELD_COLLECTION_TEMPLATE.md`](FIELD_COLLECTION_TEMPLATE.md) are the only
   route to `ML READY`. It should start before any further public-data work,
   because the outcomes cannot be acquired later.
2. **Define the target from that campaign's observed outcomes**, then the bin
   edges or risk criteria — not before. `harvest_outcome` is the only target
   that needs no invented threshold.
3. **Decide the auxiliary-schema question** (Stage 5 §12.2). Either add
   location-scoped auxiliary collections, or keep public data in
   `data/normalized/` and reference it. Minting a `SaltPan` per station should
   be recorded as rejected.
4. **Add tests for the outcome-provenance additions** from Stage 6's predecessor,
   which are still wired but untested, and filter demonstration records out of
   `ValidationService` so its total is never read as a validated event count.
5. **Make `evidential_models` a live count** once a model can be registered, so
   the status endpoint cannot drift from the registry.

---

## File manifest

```
backend/app/ml/provenance.py                  evidence classification (new)
backend/tests/test_ml_provenance.py           17 tests, the gate (new)
backend/app/services/ml.py                    records evidence; leakage re-check
backend/app/services/evaluation.py            refuses non-evidential claims
backend/app/api/routers/ml.py                 reports evidential_models
ml/training/pipeline.py                       working CLI door; clean refusals
ml/models/                                   empty; no artifact was written
```