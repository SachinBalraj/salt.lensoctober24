# SaltLens — Stage 8 Technical Audit

- Stage: 8 (final system verification and research audit)
- Date: 2026-09-30
- Scope: module-by-module audit (§A), end-to-end demonstration verification (§B), scientific/ML/field-data audit (§C), reliability changes made this stage (§D), full test-suite results (§G).
- Predecessors: `docs/ML_TRAINING_REPORT.md` (Stage 6, ML NOT READY) and `docs/ML_DEPLOYMENT_REPORT.md` (Stage 7, ML NOT DEPLOYED). This audit inherits those verdicts; nothing in Stage 8 changes them.

---

## 1. Environment under audit

- Backend: FastAPI (uvicorn `--reload`) at `http://localhost:8000`, run from `scripts/dev.sh`.
- Frontend: Next.js at `http://localhost:3000` (38 903 source bytes verified served; dashboard is client-rendered).
- Store: MongoDB Atlas (`mongodb+srv://...cluster0.mzk9n8i.mongodb.net`, database `saltlens`), reached through the application's own health/index logic.
- No git commits exist in the repository at the time of this audit (branch `main`, files untracked). All provenance therefore refers to filesystem state and live API behaviour, not VCS history.

## 2. §A — Module-by-module audit

Statuses below are read live from each router's own status endpoint (`GET /api/<module>/`), not inferred.
Test counts are the authoritative `pytest --collect-only` figures from this stage's §G run.

| # | Module | Path | Status | Primary test files | Tests |
|---|--------|------|--------|--------------------|-------|
| 1 | Authentication | `/api/auth` | **not_implemented** (planned endpoints only) | — | — |
| 2 | Salt pans | `/api/pans` | implemented | `test_salt_pans_api.py` | 50 |
| 3 | Observations | `/api/observations` | implemented | `test_observations_api.py` | 72 |
| 4 | Weather | `/api/weather` | implemented | `test_weather_api.py` | 72 |
| 5 | Digital twin | `/api/twin` | implemented | `test_digital_twin_api.py` | 40 |
| 6 | Simulator | `/api/simulator` | implemented | `test_simulator_api.py`, `test_mass_balance.py` | 31 + 29 |
| 7 | Datasets | `/api/datasets` | implemented (plus CLI ingestion/normalisation) | `test_datasets_api.py`, `test_ingestion_pipeline.py`, `test_normalize_sources.py` | 81 + 68 + 20 |
| 8 | ML training | `/api/ml` | implemented | `test_ml_api.py`, `test_ml_pipeline.py` | 31 + 73 |
| 9 | Evaluation | `/api/evaluation` | implemented | `test_evaluation_api.py`, `test_ml_evaluation.py`, `test_evaluation_absence.py` | 39 + 45 + 14 |
| 10 | Risk criteria | `/api/risk` | implemented | `test_risk_definitions.py`, `test_risk_prediction.py` | 44 + 21 |
| 11 | Explainability | `/api/explain` | implemented | `test_explain.py` | 30 |
| 12 | Decisions | `/api/decisions` | implemented | `test_decisions.py` | 70 |
| 13 | Dashboard | `/api/dashboard` | **not_implemented** (aggregation core; operator UI is client-rendered from other modules) | frontend suite | — |
| 14 | Feedback | `/api/feedback` | implemented | `test_feedback.py` | 30 |
| 15 | Outcomes | `/api/outcomes` | implemented | `test_outcomes_api.py` | 52 |
| 16 | Validation | `/api/validation` | implemented | `test_validation_api.py` | 17 |
| 17 | Model registry | `/api/models` | implemented | `test_models.py`, (together with `test_ml_api.py`) | 19 + 31 |
| 18 | Research export | `/api/export` | **not_implemented** (stub; dataset/evaluation/validation export endpoints exist and work) | `test_datasets_api.py`, `test_evaluation_api.py`, `test_validation_api.py` | — |
| 19 | Experiments | `/api/experiments` | implemented | `test_experiment_api.py` | 21 |

Cross-cutting infrastructure (shared, counted once):

| Concern | Location | Tests |
|---------|----------|-------|
| Config/DB bootstrap | `app/config.py`, `app/db/mongo.py` | `test_config.py` 4, `test_database.py` 14, `test_health.py` 7, `test_indexes.py` 12 |
| Evidence gate (ML provenance) | `app/ml/provenance.py`, `app/services/ml.py`, `app/services/deployment.py` | `test_ml_provenance.py` 17, `test_deployment_api.py` 35 |
| Security guards / rate limiting | `app/api/*`, `app/services/security*.py` | `test_security_guards.py` 19, `test_ratelimit.py` 4 |
| Research-integrity guard (demo data isolation) | `app/services/evidence*.py`, integration | `test_scientific_integrity.py` 5 |

Module-scoped tests: **969**. Cross-cutting tests: **117**. **Total backend: 1086, all passing** (§G).

### 2.1 Honest negative surfaces (verified live, not assumed)

- `GET /api/auth/` → `status: not_implemented`. The API is **OPEN ACCESS**: no route requires a token. `actor` is recorded on writes but never verified.
- `GET /api/export/` → `status: not_implemented` (anonymised CSV/JSON research export is still a stub). Working, tested export endpoints that do exist: `GET /api/datasets/{id}/export`, `GET /api/evaluation/{model_id}/export`, `GET /api/experiments/.../export`, `GET /api/validation/export`, all verified this stage (`validation/export` returns a genuine CSV header `outcome_id,pan_id,event_id,timestamp,compared,reason,predicted_number,actual_number,signed_error`).
- `POST /api/observations/ingest/sensor` → `501 "Sensor ingestion is not implemented..."`.
- `GET /api/weather/providers` → `registered: [], count: 0` (no weather API key wired; manual entry only).
- No trained models, no ML SHAP explainability output, no field-validation results exist — all such endpoints refuse with machine-readable codes rather than fabricating numbers (§C and Stage 6/7 reports).

### 2.2 Reliability finding fixed this stage (§D)

`GET /api/health` previously reported a contradiction: `database.connected: true` alongside
`indexes_ready: false` and `error: "MongoDB is unreachable."`. Root cause: the index-provisioning
exception path in `app/db/mongo.py` reused `_safe_db_error()` (which always renders the
"unreachable" sentence) for a failure that is **not** a connection failure — the ping that precedes
index provisioning had just succeeded.

Fix (see §4): the real exception is logged to the server log and the health error now reads
`"MongoDB is connected but indexes could not be provisioned"`, with `_indexes_attempted` kept
`False` so the retry path remains available. A regression test asserts the two sentences are never
conflated. Live health after the fix: `connected: true, indexes_ready: true, error: null`. An
independent fresh-connection probe against the Atlas cluster confirmed indexes are provisioned
correctly.

### 2.3 Frontend verification

Vitest: **39 test files / 816 tests passing**. `tsc --noEmit` clean, `eslint src/` clean.
The `/dashboard` page is client-rendered; the served page returns the shell (≈80-byte head) and
hydrates from the APIs audited above. Server-rendered render checks were exercised in Stage 7;
chart and interaction behaviour is covered by the component suite.

## 3. §B — End-to-end labelled demonstration (verified result)

A full operator workflow was exercised end-to-end against the live backend using a pan whose values
are explicitly a **worked example, not a field site**: `kzn-2026-demo9` "Demo Pan Delta-9"
(location description carries the words "Demonstration site (worked example, not a field site)"; area
12 000 m², normal depth 16 cm, target harvest salinity 290 g/L).

Every data item produced carries a demonstration provenance marker:

- Observation recorded with `data_source: "demonstration"`.
- Weather forecast recorded with `source: "demonstration"`, forecast 35 mm, 70 % probability.
- Digital-twin refresh reported `observation.provenance: "observed"` with `is_complete: true` and
  `source: demonstration` on the driving observation.
- Model-free mass balance: 35 mm rainfall → post-rain salinity ≈ **233.6 g/L** (from 278.5 g/L).
- Decision: **TRANSFER BRINE**, `basis: physics_and_rules`, `ml_result: null`, rule v2 (hash
  `06a32ee5…4d7b`), notice explicitly says "Physics/rule-based recommendation — ML prediction
  unavailable … DEMONSTRATION ONLY".
- Feedback recorded against the recommendation (decision `accepted`, operator `op.stage8.demo`).
  The first feedback attempt used `actual_action: "monitor"` and was rejected with **422**: the
  action vocabulary is closed (`HARVEST NOW | TRANSFER BRINE | PROTECT / DRAIN | WAIT AND RECHECK`).
  This is the validation layer working as specified, not a defect; the retry with
  `WAIT AND RECHECK` returned 201.
- Outcome recorded `is_demonstration: true`; both outcomes in the database are demonstration-labelled
  (the older `smoke-check-2026-09-29` event also carries `is_demonstration: true`).

Demo prediction evidence (all in `docs/evidence/prediction_evidence.md`): the two demonstration events
yield **one comparable pair per comparison** —

| Comparison | Predicted | Actual | Signed error |
|-----------|-----------|--------|--------------|
| Forecast vs actual rainfall (mm) | 35.0 | 32.0 | +3.0 |
| Simulated vs actual salinity, from rainfall that fell (g/L) | 236.86 | 271.2 | −34.34 |
| Simulated vs actual salinity, from forecast rainfall (g/L) | 233.58 | 271.2 | −37.62 |

The old smoke-check outcome is not comparable (no post-event salinity recorded). Every metric is
therefore n = 1 and **demonstration-only**; it is reported as such and must never be quoted as field
validation.

## 4. §C — Scientific / ML / field-data audit

- **Field data availability: none.** The research cluster holds 2 pans, 3 observations, 3 weather
  records and 2 outcomes, **all demonstration-labelled**. The Stage 5 acquisition pipeline produced
  486 553 normalised rows of public NOAA/USGS-style measurements, but **0 rows were importable** as
  SaltLens field datasets (metric/provenance mismatches documented in
  `docs/REAL_DATA_ACQUISITION_REPORT.md`). No field site has been instrumented; the field campaign
  template exists (`docs/FIELD_COLLECTION_TEMPLATE.md`) but no campaign has produced data.
- **ML status: not trained, not deployed.** `evidential_models: 0`; no dataset on the cluster passed
  the evidential gate; no model artifact exists under `ml/models/`. Training, evaluation and risk
  scoring refuse (409/code) on non-evidential input. Deployment refuses non-evidential models (Stage 7
  gate). No SHAP explanation has ever been produced because there is no registered artifact.
- **Metrics that exist are honest and clearly scoped:** the only prediction comparisons are the
  demo events in §3 (n = 1), plus Stage 6's regression metrics on held-out rows of a model trained
  from a demo dataset (documented in `docs/ML_TRAINING_REPORT.md` as demo-derived, not deployable).
- **Dataset licences/DOIs are recorded, not invented:** every normalised row carries
  `source_license` / `source_doi`; sources are the NOAA ISD (US public domain, no copyright) and
  USGS NWIS (US public domain, no copyright). No other dataset is claimed.
- **Integrity guards verified this stage:** demo rows carry distinct markers
  (`data_source`/`source`/`is_demonstration`); research-integrity and scientific-integrity suites
  (5 + 5 tests) assert these cannot be silently promoted to field measurements.

## 5. §D — Changes made during Stage 8

1. **`app/db/mongo.py`** — health-error accuracy fix (§2.2): index-provisioning failures are logged
   with their real cause and reported as an index failure, never as a connection failure.
2. **`tests/test_database.py`** — new regression test
   `test_an_index_provisioning_failure_is_not_reported_as_a_connection_failure` (14 tests in file,
   all pass).
3. **Repo-wide lint cleanup** — `ruff check app tests` went from 7 errors to **0**:
   - auto-fixed: import sorting, unused import, two `__aexit__` star-arg annotations;
   - hand-fixed: `__aenter__` → `Self` return annotation in `test_ingestion_pipeline.py`;
   - `weather=[]` mutable default → `None` in `test_scientific_integrity.py`;
   - targeted `# noqa: BLE001` on the deliberate record-and-retry except in `mongo.py`.
4. **No behavioural changes to module behaviour.** Only error-reporting accuracy and lint hygiene.

## 6. §G — Test-suite results (final, this stage)

- Backend: **1086 collected, 1086 passed** (exit 0).
- Frontend: **39 files / 816 tests passed**; TypeScript `--noEmit` clean; ESLint clean.
- Lint: `ruff check app tests` → **All checks passed**.
- Affected-file re-runs after each §D edit passed before the full suite was re-run.

## 7. Verdict

The system is mechanically healthy: every built module reports its real status, refuses honestly
where data or ML is absent, all 1086 backend and 816 frontend tests pass, and one correctness
(health-reporting) defect found this stage was fixed and regression-tested.

The system is **not** scientifically validated: there is no field data, no trained or deployed ML
model, and the only prediction metrics are demonstration-labelled (n = 1). That position is stated
exactly, never padded. Details and evidence files: `docs/evidence/`, `docs/STAGE8_DEMO_GUIDE.md`,
manuscript draft `docs/SaltLens_DSS_IEEE_manuscript.md`.