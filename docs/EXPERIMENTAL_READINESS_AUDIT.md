# SaltLens DSS — Experimental Readiness Audit

**Audit date:** 2026-10-05
**Repository:** `/Users/sachin/Documents/final mini`
**Git commit audited:** `731916d698c72d8f40437d56966c52d809f8c7e4` ("first commit", 2026-10-01 12:18:47 +0530)
**Live database audited:** MongoDB `saltlens` (read-only inspection, 2026-10-05)
**Backend:** FastAPI + MongoDB (Motor), Python 3.12.13, 21 routers, 19 collections, ~44,700 lines
**Frontend:** Next.js 16.3.6 (App Router), React 19.2.8, TypeScript, Tailwind CSS 4, recharts 3.10.1, Vitest 4

**Purpose.** Determine, from the code and the stored records rather than from the manuscript, what
SaltLens can already prove experimentally, what can be tested immediately, what requires field
measurement that has not happened, and what must not be claimed.

**Standing constraint honoured by this audit.** No rainfall, salinity, depth, transfer volume, field
event, ML metric, F1, RMSE, MAE, production yield, salt recovery or operator response has been
invented, imputed or estimated anywhere in this document. Where a figure does not exist, this audit
says it does not exist.

---

## 0. Manuscript versus implementation

Two manuscripts exist in the repository:

- `docs/SaltLens_DSS_IEEE_manuscript_FINAL.md` (232 lines) — titled *"An Evidence-Gated Digital Twin
  Decision Support System"*
- `docs/SaltLens_DSS_IEEE_manuscript.md` (139 lines) — earlier draft, titled *"An **AI-Driven**
  Digital Twin Decision Support System"*

| Manuscript statement | Implementation reality | Verdict |
|---|---|---|
| "no field measurements exist in the system" (`FINAL:31-33`) | 1 stored rainfall event and 2 manually-sourced pan observations exist. All are operator-reported, none independently verified. | **Manuscript understates.** No *verified* field measurement exists; a reported one does. |
| "no model is trained or deployed" (`FINAL:31-33`) | Confirmed. `ml_predictions`, `model_versions`, `model_evaluations`, `model_deployments`, `explanations` are all 0 in the live database; `ml/models/` holds only `.gitkeep`. | **Accurate.** |
| "ML layer reports `evidential_models = 0`" (`FINAL:30`) | Confirmed, hardcoded at `backend/app/api/routers/ml.py:106`. | **Accurate.** |
| "no field validation or ML performance is claimed" (`FINAL:31-33`) | Confirmed. §IV.E and the §V table both mark E4 (field measurements) and E5 (trained ML) ABSENT. | **Accurate and honest.** |
| n = 1 comparison block, MAE = bias = 34.345 g/L (`FINAL:144-155`) | Derived from `actual_outcomes` (2 documents), one of which has no post-event salinity at all. | **Accurate as stated, correctly labelled not simulator-skill evidence.** |
| `rule hash sha256:06a32ee5…4d7b` (`FINAL:94-97`) | Confirmed live: `sha256:06a32ee5d7f61912af5327b3ae7725c0576cc8e1640601f7de8bb96fbd9a4d7b`, rule *Operator-configured harvest and flood rule* version 2. | **Accurate.** |
| "91 OpenAPI endpoints across 21 prefixes" (`FINAL:63-66`) | 21 routers registered. | **Plausible; not re-verified in this audit.** |
| "backend pytest 1086 collected / 1086 passed; frontend 39 files / 816 tests" (`FINAL:103-105`) | **Stale and internally inconsistent.** The newest stage report (`docs/STAGE9_FIELD_VALIDATION_REPORT.md:203-205`) claims 1219 backend / 871 frontend across 44 files. `docs/DATASET_READINESS_REPORT.md:68-70` claims 1111 / 849. Three different figures for three different snapshots. | **Must be re-measured and quoted from one run.** |
| "No git history exists at audit time" (`docs/evidence/limitations.md:44`, `manuscript.md:120`) | False now: one commit `731916d` exists. | **Was true at the 2026-09-30 snapshot; now stale.** |
| `README.md:3-4` — "**An AI-Driven** Digital Twin Decision Support System" | There is no AI-driven component in operation. The FINAL manuscript dropped "AI-Driven" from its title. | **README framing is unsupported.** |
| `docs/evidence/equations_and_assumptions.md:76` — "~1.25 g/cm³ brine equivalence" | `scripts/normalize_sources.py:41` sets `PSU_TO_G_L = 1.05`. | **Documentation contradicts code.** |
| `docs/evidence/equations_and_assumptions.md:21-22` — "post-rain salt mass = C1 × V1 × 1000/1000" | `mass_balance.py:196` **assigns** `post_rain_salt_mass_kg = initial_salt_mass_kg`; it is never recomputed from `C1 × V1`. | **The "conservation check" is a tautology, not a check.** See §4.3. |

---

## 1. Existing experimental capabilities

What the system can do today, on real stored records, without any change to physics or rules.

### 1.1 A closed-form physics simulator that runs on real inputs

`backend/app/services/mass_balance.py` implements a rainfall/brine volume balance. It is pure
arithmetic, holds no state and touches no database (`mass_balance.py:229-235`). It is invoked from:

- `POST /api/simulator/mass-balance` — `backend/app/api/routers/simulator.py:97`
- `POST /api/simulator/rainfall-scenarios` (sweep) — `simulator.py:126`
- Module 12's decision path, which writes a `SimulationRun` per recommendation

**Experimental value:** a prediction can be produced from `(area, D0, C0, R)` in microseconds, with
no randomness and no learned parameters. Predictions are therefore *exactly reproducible*, which is
the precondition Phase 4's prediction-locking workflow needs.

### 1.2 An append-only, versioned, hash-identified decision-rule registry

`backend/app/models/decision_rule.py:391`. Rules are content-hashed
(`specification_hash`, `decision_rule.py:446`), versioned per name (`decision_rule.py:408`), and
supersede-able. Unique indexes on `(name, version)` and `specification_hash`
(`backend/app/db/indexes.py:297-311`) make a re-post of an unchanged rule return the existing
document rather than write a near-duplicate.

Live: 3 rules. The one in force is *Operator-configured harvest and flood rule* **version 2**,
`sha256:06a32ee5d7f61912af5327b3ae7725c0576cc8e1640601f7de8bb96fbd9a4d7b`.

**Experimental value:** every recommendation carries the rule name, version and hash that produced
it, plus a copy of the criterion rationales *as read at decision time*
(`DecisionEvaluation`, `decision_rule.py:626-661`). A recommendation cannot be silently re-derived
under a later rule.

### 1.3 A digital twin that refuses to fill gaps

`backend/app/models/digital_twin_state.py`. The twin is a **join of recorded evidence**, not a
model. Missing inputs stay `None` and are listed in `missing_inputs` (`digital_twin_state.py:190`).
Provenance travels per value: `observation_id`/`observation_timestamp`/`observation_data_source`
(`:175-177`), `weather_id`/`weather_timestamp`/`weather_source` (`:179-181`).

**Experimental value:** a prediction can name exactly which observation and which weather record fed
it. That is what makes a multi-event validation table auditable row by row.

### 1.4 A field-event ledger with interventions, corrections and timestamp reconciliation

`backend/app/models/field_validation.py`, `backend/app/services/field_validation.py`, exposed at
`/api/field-validation`. Features that exist and are non-trivial:

- `RainfallEvent` with pre/post snapshots, interventions, verification state
- Interventions recorded with `kind`, `direction`, `performed_at`, `volume_unit` — and a volume that
  may legitimately be absent
- Append-only `corrections` list with `previous_value`, `new_value`, `reason`, `corrected_by`,
  `evidence_source` — 6 corrections on the one stored event
- `RainfallTimestampConflict` storing **both** the stored and the reported instant with
  `offset_seconds`, resolved only against evidence
- `SupersededRainfallTimestamp` with `meaning: "unknown"` (`field_validation.py:292`), so a replaced
  value can never be read as current
- A refusal path: a 30 September observation **cannot** be attached to a 14 March event
  (`services/field_validation.py:1108-1176`)

### 1.5 A full provenance/audit layer

Every document carries created/updated timestamps and, where it matters, a `notice` stating what the
record was built from. `Recommendation` carries four separately identified evidence blocks — `twin`,
`simulation`, `ml`, `evaluation` (`recommendation.py:309-344`) — with `ml` explicitly nullable, and
that null is what `basis: physics_and_rules` *means* (`recommendation.py:317-324`).

`OutcomeLink` (`actual_outcome.py:191`) links an outcome to pan state, weather forecast, simulation
run, recommendation and decision, each with `kind`, `record_id`, `timestamp` and a summary.

### 1.6 A computed-metric discipline already in the codebase

`MetricReading` (`backend/app/models/evaluation.py:87`) requires `computed: bool` **plus a reason**,
enforced by `_enforce_reading` (`evaluation.py:115`). No metric can be published without a named
denominator. `DataSourceSummary.demonstration_records_excluded` (`dataset.py:477-486`) counts
demonstration records rather than silently dropping them.

**This is the single most reusable asset for Phase 6.** The insufficiency gate the paper needs
already has a house style.

### 1.7 A demonstration-labeling convention

`DecisionTwinSummary.is_demonstration` / `demonstration_sources` (`recommendation.py:118-129`),
`DataSourceSummary.demonstration_records_excluded`, `OperatorDecision.is_demonstration`. Clients
label from a flag rather than inferring from source strings.

### 1.8 An existing test suite

- Backend: **36 test modules** under `backend/tests` (`pytest.ini`, `testpaths = tests`,
  `pythonpath = .`). The suite never touches a live cluster: `backend/tests/conftest.py:27` sets
  `SALTLENS_TEST_NO_DATABASE=1` at import time, because `app.config` is `lru_cache`d and the
  database manager is built at import.
- Frontend: **47** `*.test.ts(x)` files under `frontend/src`.
- Notable integrity modules: `test_scientific_integrity.py`, `test_security_guards.py`,
  `test_ml_provenance.py`, `test_normalize_sources.py`.
- Repo-root `tests/` exists but is **empty** and untracked (`README.md:157` describes it as
  "next stage").

---

## 2. Existing data

### 2.1 Live database record counts (read-only, 2026-10-05)

| Collection | Count | Note |
|---|---:|---|
| `salt_pans` | 4 | |
| `pan_observations` | 7 | **2 manual, 5 `demonstration`** |
| `weather_observations` | 5 | **1 manual, 4 `demonstration`** |
| `rainfall_events` | 1 | the 14 March 2026 event |
| `simulation_runs` | 42 | all `is_simulated: true` |
| `recommendations` | 39 | |
| `operator_decisions` | 11 | |
| `actual_outcomes` | 2 | |
| `decision_rules` | 3 | v2 in force |
| `research_datasets` | 2 | both named `demo-isolation-*` |
| `digital_twin_states` | 6 | |
| `ml_predictions` | **0** | |
| `model_versions` | **0** | |
| `model_evaluations` | **0** | |
| `model_deployments` | **0** | |
| `explanations` | **0** | |
| `research_experiments` | **0** | |
| `risk_definitions` | **0** | |
| `users` | **0** | |

### 2.2 Pans

| `pan_id` | name | area m² | normal depth cm |
|---|---|---:|---:|
| `pan_orginal` | north pan | 12,300 | 15.0 |
| `field-p01` | Actual salt-pan name | 12,500 | 15.5 |
| `kzn-2026-demo9` | Demo Pan Delta-9 | 12,000 | 16.0 |
| `pan-demo-01` | Demo Salt Pan 01 | 10,000 | 20.0 |

**`pan_orginal` is the only pan carrying manually-sourced observations.** Note the identifier is a
typo in the source data and is preserved verbatim everywhere (`normalize_pan_id` only lowercases and
strips).

### 2.3 The two manual observations (the entire non-demonstration measurement base)

| Field | `obs-fd6c902814f84ad1bf1db05fb8d1244a` | `obs-8d0efe4005c04caeb30adcae65647dea` |
|---|---|---|
| pan | `pan_orginal` | `pan_orginal` |
| timestamp | 2026-03-13T08:00:00Z | 2026-09-30T09:10:27.774Z |
| salinity | 250.0 g/L | 250.0 g/L |
| depth | 20.0 cm | 20.0 cm |
| `data_source` | `manual` | `manual` |
| observer | `site operator` | — |
| evidence | *North Pan field notebook (manual field measurement)* | — |
| note | "Pre-event brine for the 14 March 2026 rainfall event, as recorded in the original North Pan field notebook." | "Actual pre-rainfall measurements collected from North Pan. Salinity, water depth and temperature were recorded manually for SaltLens DSS research and model validation." |

> **Open provenance question — must be resolved before any field claim is made.**
> The second observation carries `brine_temperature_c: 32.0`, `humidity_percent: 60.0`,
> `ambient_temperature_c: 27.0`. **The identical triple `(32.0, 60.0, 27.0)` appears on three
> records explicitly marked `data_source: demonstration`** (`obs-53d441ba…`, `obs-acba8621…`,
> `obs-83eeb8c6…`). The salt/depth pair `(250.0, 20.0)` also appears on the demonstration
> `obs-acba8621…` and `obs-83eeb8c6…`. This does not prove the record is untrustworthy, and it is not
> asserted here as fabrication. It is an unresolved provenance inconsistency: the field operator must
> confirm whether the ancillary values are measured or copied from a demonstration fixture. Until
> then, only salinity and depth should be treated as evidence for that record.

### 2.4 The one manual weather record

`wx-6747268cacee4b919656d6ae47a58bff` — `pan_orginal`, timestamp 2026-09-30T09:00:00Z,
`forecast_for` 2026-10-01T02:30:00Z, `rainfall_mm` **25.0**, `rainfall_probability_percent` 70.0,
`source: manual`, `is_forecast: true`.

**This is the September forecast.** It is a forecast, not an observation.

### 2.5 The 14 March 2026 event — the only rainfall event on file

`event_id: ev-249b575de8234ff09b341e525ef5d4a1`, `pan_id: pan_orginal`,
name *"North Pan 14 Mar 2026 rainfall event"*, `reported_by: field-operator`,
`verification_state: reported`.

| Item | Value |
|---|---|
| `event_timestamp` | 2026-03-14T18:00:00Z = **23:30 IST** |
| `event_milestone` | `rainfall_start` (was `unknown` until notebook reconciliation) |
| `actual_rainfall_mm` | **90.0** |
| `forecast_rainfall_mm` | **40.0** |
| `rainfall_source` | "manual rain gauge" |
| `rainfall_timestamp_evidence` | North Pan field notebook (manual field measurement) |
| `rainfall_timestamp_verification` | `operator_reported` |
| pre-event | **250.0 g/L @ 20.0 cm**, 2026-03-13T08:00:00Z, `manual`, linked to `obs-fd6c90…`, `timestamp_verified: true` |
| post-event | **20.0 g/L @ 50.0 cm**, 2026-03-15T08:00:00Z, `manual`, **`timestamp_verified: false`** |
| intervention | `brine_transfer_out`, `direction: out`, performed 2026-03-14T19:00:00Z, **volume not recorded** |
| `corrections` | 6 |
| superseded rainfall instant | 2026-03-14T14:00:00Z (19:30 IST), `meaning: unknown` |
| timestamp conflict | `rfc-edf3c8b8…`, offset 14,400 s (4 h), status **resolved** |

**Independently verified: NO.** `verification_state` is `reported`, the rainfall timestamp
verification is `operator_reported` with `rainfall_timestamp_observed: false`, and the post-event
timestamp is explicitly uncorroborated.

**Derived replay figures** (recomputed here from the stored inputs, using
`mass_balance.py`; area 12,300 m², D0 = 0.20 m, C0 = 250.0 g/L, R = 90 mm **actual**):

```
V0 = 12300 × 0.200 = 2460.0 m³
Vr = 12300 × 0.090 = 1107.0 m³
V1 = 2460.0 + 1107.0 = 3567.0 m³
C1 = 250.0 × 2460.0 / 3567.0 = 172.41379310344828 g/L   ✓ matches stored 172.41379310344828
post-rain depth = 20.0 + 9.0 = 29.0 cm                   ✓ matches stored 29.0
```

Absolute differences against the reported post-event figures:

```
salinity |20.0 − 172.41379310344828| = 152.41379310344828 g/L
depth    |50.0 − 29.0|               =  21.0 cm
```

**These two numbers must never be presented as simulator accuracy.** The difference is dominated by
an unrecorded brine transfer out of unknown volume one hour after the event instant, and the
observation itself is uncorroborated. `data/exports/field-validation.csv` already carries these
figures; the interpretation is what is missing.

### 2.6 The September scenario — real records, forecast-based demonstration

Fully reconstructable from live storage:

| Stage | Record | Content |
|---|---|---|
| Pre-rain observation | `obs-8d0efe4005c04caeb30adcae65647dea` | 250.0 g/L @ 20.0 cm, 2026-09-30T09:10:27.774Z, manual |
| Forecast | `wx-6747268cacee4b919656d6ae47a58bff` | **25.0 mm**, forecast_for 2026-10-01T02:30:00Z, manual |
| Twin | `twin-4c6b60e181974e05b347169e369be583` | 250 g/L, 20 cm, `forecast_rainfall_mm: 25.0`, `is_demonstration: false` |
| Simulation | `run-3cb1a300946e4bd4ab978f17f4cf9bf4`, `run-78839794570a407cab9fc511595243b3` | **222.22222222222223 g/L @ 22.5 cm**, dilution 11.111%, model `rainfall-dilution-mass-balance-v1`, `rainfall_from: twin.forecast_rainfall_mm` |
| Rule | *Operator-configured harvest and flood rule* v2, `sha256:06a32ee5…4d7b` | criterion `simulated_post_rain_salinity_g_l <= target_harvest_salinity_g_l` (280.0, `pan_registration`) → **HARVEST NOW** |
| Recommendation | `rec-fdccf49b4fc2471da15a5f95b1b2a39f`, `rec-56c91da13cd147128b3d92755d445545` | `basis: physics_and_rules`, `ml_result: null` |
| Operator decision | `decision-4b099249f7284361b0c5e854463cb0e4` | `accepted`, `actual_action: HARVEST NOW`, operator `Sachin S B`, 2026-09-30T11:00:42.948Z |
| Decision note | | *"Recommendation accepted for SaltLens DSS workflow testing. **No physical harvesting was performed.** Based on the versioned rule and 25 mm rainfall forecast."* |

Arithmetic check: `250.0 × 0.200 / 0.225 = 222.222…`; `20.0 + 2.5 = 22.5 cm`. Reproducible by hand.

**There is NO post-event observation for September.** The 25 mm has not been confirmed to have fallen.
This chain is a forecast → prediction → rule → operator-acceptance trace and nothing more.

### 2.7 Data directories on disk

- `data/normalized/` — **28 CSVs, 486,581 non-header rows** from six public sources (PANGAEA 922231
  / 922264 / 989538, NOAA ISD × 5 stations, USGS NWIS, RCS Rhine-Meuse Delta 2022). Every row carries
  `source_file`, `source_doi`/`source_url`, `source_license`, lat/lon.
- `docs/evidence/dataset_provenance.md` states **0 rows were importable** as SaltLens training
  datasets: public hydrology does not carry salt-pan brine salinity and depth in the required units.
- `data/processed/` — **`.gitkeep` only.** No processed dataset exists.
- `data/templates/` — 4 header-only CSVs (`pans`, `observations`, `weather`, `outcomes`), zero data
  rows.
- `data/exports/field-validation.csv` — 1 data row (the March event, 37 columns).
- `data/raw/public_reference/` — empty.
- No SQLite, no `.db`, no SQL seed file, no JSON seed file anywhere.

### 2.8 ML artefacts

| Location | Content |
|---|---|
| `ml/models/` | `.gitkeep` only |
| `ml/notebooks/` | `.gitkeep` only |
| `ml/features/__init__.py` | **0 bytes** |
| `ml/training/__init__.py` | stub; its one function raises `NotImplementedError` |
| `ml/training/pipeline.py` | CLI wrapper delegating to `app.ml.pipeline.MLService.train`; `evaluate_candidate()` raises `NotImplementedError` (`ml/training/pipeline.py:111-125`) |
| `ml/ingestion/` | real, ~2,830 lines: `audit`, `validate`, `stage`, `import` CLI with per-kind record specs |

---

## 3. Existing simulation equations

Reproduced from `backend/app/services/mass_balance.py:8-12` and `:185-226`.

```
V0  = A × D0                              initial brine volume      (m³)   [line 186]
Vr  = A × R                               rainfall volume           (m³)   [line 188]
V1  = V0 + Vr                             post-rain volume           (m³)   [line 190]

initial_salt_mass_kg    = C0 × V0 × 1000 / 1000                     (kg)   [lines 193-195]
post_rain_salt_mass_kg  = initial_salt_mass_kg   (ASSIGNED)          (kg)   [line 196]

C1  = C0 × V0 / V1                        post-rain salinity        (g/L)   [line 199]
dilution % = ((C0 − C1) / C0) × 100                                 (%)     [lines 205-208]
post-rain depth = D0 + R                                          (cm→m)   [line 225]
```

Input guards (`:163-183`): `area_m2 > 0`, `initial_depth_m > 0`, `initial_salinity_g_l >= 0`,
`rainfall_m >= 0`. A violation raises `RainfallDilutionError` (`:90`) instead of returning a number.

`dilution_percent` is `None` — never `0` — when `C0 == 0`, with reason
`DILUTION_UNDEFINED_FOR_FRESH_BRINE` (`:83-87`, applied at `:201-203`).

Model name: `MASS_BALANCE_MODEL_NAME = "rainfall-dilution-mass-balance-v1"` (`:51`).
Preset sweeps: `STANDARD_RAINFALL_SCENARIOS_MM = (0.0, 5.0, 10.0, 20.0, 50.0, 100.0)` (`:39`).
Disclaimer shipped with every result: *"Physics-based simulation under simplifying assumptions;
not an ML prediction."* (`:41-43`).

### 3.1 The eight published assumptions (`:56-80`)

1. Complete mixing — rain and brine reach one uniform salinity instantly.
2. No evaporation during the event — rainfall is the only water that moves.
3. No drainage.
4. No overflow — the pan holds every drop, no brine lost over the wall.
5. No seepage.
6. **No brine transfer** — brine is neither pumped in nor drawn off.
7. No crystallization — salt stays dissolved.
8. Rainfall is a single instantaneous depth — *when* it fell is not resolved.

**Assumption 6 is the reason the March event cannot grade the simulator.** A brine transfer out
occurred at 2026-03-14T19:00:00Z. The model explicitly excludes transfer, and the transferred volume
was not recorded, so the post-event observation cannot be attributed to rainfall.

### 3.2 Depth calculation

There is no separate depth model. `post_rain_depth_m = initial_depth_m + rainfall_m`
(`mass_balance.py:225`) — an exact addition under assumption 8.

### 3.3 Rainfall logic

- `WeatherObservation.rainfall_mm: float | None`, bounded by `RAINFALL_RANGE`,
  `allow_inf_nan=False` (`backend/app/models/weather.py:67`)
- `_infer_forecast_flag` (`weather.py:87-104`) marks a record `is_forecast=True` whenever
  `forecast_for` is in the future, **regardless of the caller's flag**
- `_validate_forecast` (`:109`) refuses a forecast row without `forecast_for`;
  `_require_a_measurement` (`:119`) refuses a weather row carrying no measurement at all
- Same bounds reused in `DigitalTwinState.forecast_rainfall_mm`
  (`digital_twin_state.py:157`), `ActualOutcome.forecast_rainfall_mm`/`actual_rainfall_mm`
  (`actual_outcome.py:247`, `:262`), `RainfallEvent.actual_rainfall_mm`/`forecast_rainfall_mm`
  (`field_validation.py:394`, `:422`)
- `sensor_ingest_enabled = False` by default (`backend/app/config.py:58`), so
  `POST /api/observations/ingest/sensor` refuses `data_source="sensor"` rather than accepting a
  reading no instrument produced

**No live weather provider is wired.** `GET /api/weather/providers` returns an empty registry.

---

## 4. Existing ML functionality

### 4.1 What exists

`backend/app/ml/` is **4,172 lines of real, substantive ML infrastructure**:

| File | Lines | Role |
|---|---:|---|
| `pipeline.py` | 578 | train orchestration, `InsufficientDataError`, split execution |
| `evaluation.py` | 828 | held-out scoring, metric computation |
| `cross_validation.py` | 658 | repeated / grouped CV |
| `splitting.py` | 448 | `SPLIT_STRATEGIES`, leakage-aware splitting |
| `preprocessing.py` | 334 | feature preparation |
| `provenance.py` | 351 | per-row provenance and genuine-vs-demonstration classification |
| `candidates.py` | 307 | four classifier candidates |
| `sufficiency.py` | 235 | the data-sufficiency gate |
| `comparison.py` | 358 | model/version comparison |

Live policy in `backend/app/api/routers/ml.py:53-73`: four candidates
(`logistic_regression`, `decision_tree`, `random_forest`, `gradient_boosting`), selection by highest
macro F1, Random Forest fitted but never auto-selected. `POST /api/ml/train` either trains or returns
**422** with the fixed sentence *"Insufficient labelled data for reliable model training."* — no
partial success, no artifact, no registered version.

`ml/README.md:16-20` states the policy verbatim:

> "never fake inputs, predictions, SHAP values, or metrics. Train only on stored datasets; evaluate
> only on a held-out split from real data; export held-out scores from the run that fitted the model,
> not from constants. No synthetic rows, labels, oversampling, augmentation, or weak fallback models."

### 4.2 What does NOT exist — audited item by item

| Requirement | Present? | Evidence |
|---|---|---|
| Trained ML model | **NO** | `model_versions` = 0; `ml/models/` = `.gitkeep` only |
| Training dataset | **NO** | `data/processed/` empty; the 2 `research_datasets` are `demo-isolation-check` and `demo-isolation-wide` |
| Labels | **NO** | the single `actual_outcome` has `harvest_outcome: null` (`docs/ML_TRAINING_REPORT.md:40`) |
| Train/validation/test split | **NO artifact** | splitting *code* exists; no split has been produced |
| Model checkpoint | **NO** | no `.joblib`/`.pkl`/`.onnx`/`.pt` anywhere in the repo |
| Prediction pipeline | **code only** | `ml_predictions` = 0; explain endpoint refuses |
| Evaluation code | **YES** | `evaluation.py`, `cross_validation.py` — but nothing has been evaluated |
| Genuinely labelled rows | **0** | `docs/DATASET_READINESS_REPORT.md:16-18`: need ≥80, have 0; need ≥3 distinct pans, have 0 |
| `ml_predictions` / `explanations` | **0** | live |

### 4.3 A finding about the conservation check

`mass_balance.py:193-196` **assigns** `post_rain_salt_mass_kg = initial_salt_mass_kg`. The published
invariant `salt_mass_conserved` (`:138-148`) compares that assignment to itself, so it is **always
true by construction** — it is a tautology, not an independent verification.

This is not a numerical error: `C1 × V1 = (C0·V0/V1)·V1 = C0·V0`, so the values agree exactly. But a
reader is told a conservation check ran when none did. `docs/evidence/equations_and_assumptions.md:21-22`
compounds this by stating the post-rain mass is computed as `C1 × V1 × 1000/1000`, which the code never
does. **Recommendation:** recompute `post_rain_salt_mass_kg` from `C1 × V1` rather than assigning it.
This is a one-line change that makes the published check real. It does not alter any predicted
salinity or depth.

---

## 5. Existing recommendation functionality

### 5.1 Action vocabulary (closed, `backend/app/models/enums.py:267-298`)

`HARVEST NOW` · `TRANSFER BRINE` · `PROTECT / DRAIN` · `WAIT AND RECHECK`.
No `DEFAULT` member and no implicit ordering. `is_inaction` marks the three invasive actions
(`:289-298`). `DecisionBasis` has exactly two members: `PHYSICS_AND_ML`, `PHYSICS_AND_RULES`
(`:323-340`).

### 5.2 The engine deliberately holds no thresholds

`backend/app/services/decisions.py:1-33` — the engine's own opinion is nil. It holds no threshold, no
default action and no ordering, because a threshold written into that file could not be revised
without invalidating every recommendation already issued.

`DecisionVariableSource` (`enums.py:301-321`) has exactly four values: `twin`, `simulation`,
`prediction`, `pan_registration`. The catalogue (`backend/app/services/decision_catalog.py:87-376`)
declares 13 variables, each naming its source collection and field. No variable is derived from
another by arithmetic.

The reference rule (`backend/app/services/decision_reference_rule.py`) carries **no thresholds of its
own** (`:1-16`). Four criteria, all bound-only (`:113-167`):

| Criterion | Bound | Action | Rank |
|---|---|---|---|
| `simulated_post_rain_salinity_g_l <= target_harvest_salinity_g_l` | pan registration | HARVEST NOW | 1 |
| `simulated_post_rain_depth_cm >= flood_depth_cm` | pan registration | PROTECT / DRAIN | 3 |
| `simulated_post_rain_salinity_g_l < dilution_salinity_g_l` | pan registration | WAIT AND RECHECK | 0 |
| `simulated_post_rain_depth_cm >= target_flow_cm` | pan registration | TRANSFER BRINE | 2 |

`unmatched_action = WAIT_AND_RECHECK` (`:168`), though an unconfigured bound suppresses it entirely.

### 5.3 Two evaluation rules that matter for experiment integrity

`backend/app/services/decision_evaluation.py:15-21` — an **unread** criterion variable is *undecided*,
not a non-match, and suppresses the fallback. A missing reading never silently becomes "no match".
`:23-26` — **every** matching criterion is retained, not only the deciding one. The live
`kzn-2026-demo9` recommendation shows three criteria matched at once, ranked to PROTECT/DRAIN (3) >
TRANSFER BRINE (2) > HARVEST NOW (1).

### 5.4 ML is separated from rules, honestly

`ML_UNAVAILABLE_LABEL = "Physics/rule-based recommendation — ML prediction unavailable."`
(`decisions.py:101`), with three named reasons in `ML_UNAVAILABLE_REASONS` (`:110-126`):
`no_prediction_stored`, `class_only_model`, `rule_does_not_read_ml`. The live September
recommendation carries that notice verbatim, and its `rule does not read ml` clause is correct — the
active rule declares no prediction variable.

---

## 6. Existing operator workflow

1. A recommendation is issued with `issued_at`, `expires_at` and the rule identity
   (`recommendation.py:384-389`, `:461`).
2. Exactly one decision per recommendation is possible. Guaranteed by a **unique index** on
   `operator_decisions.recommendation_id`, named `operator_decisions_one_decision_per_recommendation`
   (`backend/app/db/indexes.py:320-343`). The service check backs it up; the index is what actually
   enforces it under concurrency.
3. `OperatorDecision.decision ∈ {accepted, modified, rejected}` (`operator_decision.py:29`).
   `decision = None` means *unfinished*. `actual_action = None` **explicitly means no physical action
   was taken** (`:27-37`).
4. `_modification_or_rejection_names_its_action` (`:103`) — a modification or rejection must state
   what was actually done.
5. `HarvestOutcome ∈ {none, partial, full, lost}` (`enums.py:352-356`) — **this is the only
   production-outcome vocabulary in the system, and it is recorded nowhere.**
6. Live: 11 decisions. 10 are `is_demonstration: true`. **Exactly one is not:**
   `decision-4b099249f7284361b0c5e854463cb0e4` — the September acceptance by `Sachin S B`.

### 6.1 Timestamp reconciliation

`utcnow()` returns timezone-aware UTC; naive datetimes are interpreted as UTC and aware ones
converted (`backend/app/models/base.py:12-22`). `FieldMeasurement` carries `recorded_timezone` and
`timestamp_verified` (`field_validation.py:167-180`). Conflicts store both instants with
`offset_seconds` and refuse to record a conflict that is not a real disagreement
(`field_validation.py:349`). Pre-event linkage requires a stored pre-rain observation plus declared
local time and timezone, and rejects a timestamp mismatch
(`services/field_validation.py:1150-1176`).

---

## 7. Existing limitations

### 7.1 Data limitations

1. **One rainfall event exists.** `rainfall_events` = 1. A multi-event validation study is impossible
   from stored data.
2. **That one event is unusable as accuracy evidence** — intervention with unrecorded volume,
   uncorroborated post-event timestamp, `verification_state: reported`, not independently verified.
3. **Zero independently verified events.** This is the binding constraint.
4. **Two manually-sourced observations**, both `pan_orginal`, both 250 g/L @ 20 cm. One pan. No
   between-pan variance, therefore no generalisation claim is possible.
5. **486,581 normalised public rows, 0 importable.** No salt-pan brine salinity/depth in g/L and cm.
6. **Zero production, yield, salt-recovery or brine-loss data of any kind.**
7. **The ancillary-value provenance inconsistency in §2.3** is unresolved.

### 7.2 Model limitations

8. The mass balance excludes evaporation, drainage, overflow, seepage, brine transfer and
   crystallization. On a real pan the largest of these during a rain event is usually brine transfer
   or drainage — precisely what was unrecorded in the March event.
9. Rainfall is instantaneous; intensity, duration and antecedent dryness are unmodelled.
10. **The conservation check is a tautology** (§4.3).
11. No pan is 100% flat in reality; `A × D` as the whole volume is an idealisation.

### 7.3 ML limitations

12. No model, no dataset, no labels, no checkpoint, no split, no metric. 4,172 lines of
    infrastructure and zero results.
13. `research_datasets` contains only two `demo-isolation-*` records — demonstration scaffolding.
14. `ml/training/pipeline.py` requires `numpy`/`pandas`/`scikit-learn`/`joblib`/`shap`/`matplotlib` at
    module scope, but those live in `requirements-ml.txt`, not `requirements.txt`. **Installing only
    `requirements.txt` yields a backend whose `/api/ml`, `/api/evaluation` and `/api/explain` routes
    fail to import.** `scripts/setup.sh` installs both, so `make setup` is fine; a manual
    `pip install -r requirements.txt` is not.

### 7.4 Operational limitations

15. **No authentication.** The API is open access; no route requires identity or role
    (`README.md:577-598`).
16. **No weather provider wired.** `registered: []`.
17. **No sensor gateway.** `POST /api/observations/ingest/sensor` returns 501.
18. **`HarvestOutcome` is recorded nowhere** — the single field that could carry production evidence
    is unused.
19. **Operator decisions are 10/11 demonstration.** One real acceptance, no rejection, no
    modification, no timing, no confidence.
20. There is no `decision_time` field anywhere in the schema — decision latency cannot currently be
    measured even in principle.

### 7.5 Documentation limitations

21. Three different backend test counts are quoted across documents (1086 / 1111 / 1219).
22. `docs/evidence/equations_and_assumptions.md:76` contradicts `scripts/normalize_sources.py:41`.
23. `docs/evidence/equations_and_assumptions.md:21-22` misdescribes the salt-mass computation.
24. `docs/evidence/limitations.md:44` and `manuscript.md:120` claim no git history; one commit exists.
25. `README.md:3` still says "AI-Driven"; `README.md:628-632` links 3 of the 23 documents it contains.
26. Repo-root `tests/` is empty and untracked while `README.md:157` lists it in the tree.

---

## 8. What can be experimentally tested immediately

Everything below runs today against real records, with no new field data and no change to physics or
rules.

| # | Experiment | Why it is possible now | What it proves |
|---|---|---|---|
| E1 | **Physics correctness** — recompute every stored `SimulationRun` from its stored inputs and assert equality | `simulation_runs` = 42, each with full `inputs`/`outputs` | The simulator is deterministic and its stored outputs are reproducible. Hand-checkable. |
| E2 | **September trace replay** — reproduce 25 mm → 222.222… g/L @ 22.5 cm from the stored twin + rule | Every link in the chain exists live (§2.6) | The forecast→prediction→rule→acceptance trace is reproducible end to end. **Forecast-based demonstration only.** |
| E3 | **Rule determinism** — re-evaluate the active rule against each stored twin | Rule v2 + hash + catalogue all stored | Recommendations are a deterministic function of recorded evidence. |
| E4 | **Multi-event scaffolding + event classification** — register real events, lock predictions, classify A/B/C | `MassBalanceService` is stateless; store is buildable | The *apparatus* for multi-event validation, proven on the one real event. |
| E5 | **Prediction-locking / leakage prevention** — prove an observation cannot retro-edit a locked prediction | New store + workflow | The integrity property that makes Phase 4 credible. |
| E6 | **Metric calculation** — AE/MAE/RMSE/Bias over the events that qualify | Real predictions and one real observation exist | The metric code is correct, exercised on real numbers, and correctly reports insufficiency. |
| E7 | **ML readiness gate** — report the audit from live collections | `model_versions`, `research_datasets` etc. all countable | A machine-readable statement that no ML metric may be reported, with counts. |
| E8 | **Provenance / audit-trail integrity** — verify every stored recommendation cites a live rule hash, every simulation cites the live model name | 39 recommendations, 42 runs | Provenance is enforced, not decorative. |
| E9 | **Demonstration-exclusion accounting** — count demonstration vs non-demonstration across all evidence | `is_demonstration` flags are stored | The system knows exactly how much of its data is not evidence. |
| E10 | **Operator-decision audit** — dispositions, actions, rule identity, demonstration flag | 11 decisions | One real acceptance; zero rejections; no production outcome. |
| E11 | **Test-suite reproducibility** — quote one measured run | 36 backend modules, 47 frontend files | A single, current, honest number. |

**E1–E3, E7–E11 are software-verification experiments. They are legitimate evidence about the
system. They are not evidence about salt pans.** The distinction must be preserved in the paper.

---

## 9. What requires real field measurements

Each item below is impossible without new measurement. None can be simulated, and none may be
estimated.

| # | Needed | Why | Minimum collection |
|---|---|---|---|
| F1 | **Multiple rainfall events on real pans** | `rainfall_events` = 1 | ≥5 events, ≥3 distinct pans, across ≥2 seasons |
| F2 | **Independently verified observations** | `independently_verified` = false everywhere | Second observer, or instrument log, per event. `verification_state` must reach `verified`, not `reported`. |
| F3 | **Recorded intervention volumes** | The March transfer volume is absent | `transfer_volume_m3` and `drainage_volume_m3` measured, not estimated |
| F4 | **Confirmed post-event timestamps** | March post-event `timestamp_verified: false` | Independent time source per reading |
| F5 | **Rain-gauge records** | March rainfall source is `operator_reported`, `observed: false` | Gauge log or official station record |
| F6 | **Confirmed rainfall totals** | 90 mm is operator-reported | Gauge |
| F7 | **Evaporation / seepage characterisation** | Model excludes both | At least paired pre/post depth readings with rainfall and wind |
| F8 | **Production outcome** | `HarvestOutcome` recorded nowhere | Salt mass harvested (kg), brine volume, per pan per event |
| F9 | **Salt recovery and brine loss** | No field exists for either | Weighed harvest + metered transfer |
| F10 | **Operator study with real operators** | 10/11 decisions are demonstration | N operators, documented scenario set, timed decisions, recorded confidence |
| F11 | **Control comparison for operational benefit** | Single-arm, no baseline | A with/without-DSS arm across ≥1 season |
| F12 | **ML labels** | 0 genuinely labelled rows | Post-event salinity/depth/risk-class labels on F1 events, plus ≥80 rows over ≥3 pans |

**Until F1–F6 exist, no predictive-accuracy claim is possible.** Until F8–F9 exist, no production or
harvesting-benefit claim is possible. Until F10–F11 exist, no operational-benefit claim is possible.

---

## 10. What must NOT be claimed in the IEEE paper

### 10.1 Prohibited outright

| Prohibited claim | Why |
|---|---|
| Any ML accuracy, F1, precision, recall, ROC-AUC, R² or SHAP value | No model, no dataset, no labels, no split, no checkpoint. `evidential_models = 0`. |
| "AI-driven", "machine-learning-based", "learned", "data-driven prediction" | Nothing is learned. `simulation.is_ml` is `false` on every stored run. |
| Any multi-event validation result | `rainfall_events` = 1. |
| "Validated against field data" | 0 independently verified events. `verification_state: reported`. |
| Any MAE / RMSE / bias as **simulator skill** | The only real observation pair is intervention-affected with unrecorded volume. |
| Any production, yield, salt-recovery or brine-loss figure | No such data exists anywhere. |
| Any operational-benefit, decision-time or workload-reduction figure | No `decision_time` field, no baseline arm, 10/11 decisions are demonstration. |
| Any generalisation or transfer claim | One pan. |
| Any claim that SaltLens improves harvest timing or salt yield | Unmeasured. |
| Citing the 90 mm rainfall as a gauge measurement | `rainfall_source: "manual rain gauge"` is an operator claim; `rainfall_timestamp_observed: false`. |
| Citing `salt_mass_conserved: true` as verification | It is a tautology (§4.3). |
| Any reference or citation | No bibliography exists; per standing constraint, none may be fabricated. |
| The 152.41 g_L / 21.0 cm figures as accuracy | They are arithmetic on an unusable observation pair. |

### 10.2 Required precision of wording

| Instead of | Write |
|---|---|
| "validated on field data" | "replayed against one operator-reported event whose post-event state is confounded by an unrecorded brine transfer" |
| "accurate salinity prediction" | "a closed-form first-order dilution balance, reproducible by hand" |
| "AI-driven decision support" | "evidence-gated decision support: a transparent mass balance plus a versioned, operator-configured rule" |
| "the system validated the model" | "the comparison machinery was exercised on the one available event and correctly refused to treat it as validation evidence" |
| "operator acceptance rate" | "one operator acceptance was recorded; ten further decisions are demonstration records" |
| "September scenario results" | "September forecast-based software demonstration: a 25 mm forecast produced a 222.22 g/L / 22.5 cm prediction, which rule v2 turned into HARVEST NOW, which the operator accepted. No rain was observed to fall and no harvesting was performed." |

### 10.3 What MAY be claimed, with the evidence named

- The architecture, the evidence gate and the refusal paths — **SUPPORTED BY SOFTWARE TEST**
- The mass balance's correctness and hand-checkability — **SUPPORTED BY SOFTWARE TEST** (and E1)
- Determinism and full reproducibility of prediction → rule → recommendation — **SUPPORTED BY SOFTWARE TEST**
- The September 25 mm trace as a **forecast-based demonstration** — **SUPPORTED BY SOFTWARE TEST**
- One operator acceptance recorded — **SUPPORTED BY FIELD RECORD**, n = 1, not a rate
- That the system correctly reports insufficiency and refuses to fabricate — **SUPPORTED BY SOFTWARE TEST**
  and is, in itself, the most defensible contribution available

---

## 11. Bottom line

The honest position is narrow and it is strong:

- **0 independently verified rainfall events.** Accuracy validation is currently **impossible**.
- **0 trained ML models.** ML evaluation is currently **impossible**.
- **0 production data points.** Operational-benefit measurement is currently **impossible**.
- The 4,172-line ML layer, the 21-router API, the provenance/audit machinery and the refusal paths are
  all real, all tested, and all **verifiable today**.

The three paper weaknesses are not addressable by any amount of software work. They require field
measurement. What the software *can* do now is make that field campaign protocol-ready and make it
impossible to overstate the result when it is eventually run — which is the contribution Phases 2–14
of this work deliver.
