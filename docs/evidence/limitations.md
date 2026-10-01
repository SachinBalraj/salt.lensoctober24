# SaltLens — Outstanding Limitations (stated plainly)

All are as of 2026-09-30. None is hidden or padded; each has a mechanism that refuses to overclaim.

## 1. No field data

- Zero field-collected observations exist. The two pans, three observations, three weather records
  and two outcomes are demonstration-labelled.
- 486 553 normalised public-hydrology rows could not be imported as training datasets (unit/semantics
  mismatch with brine salinity/depth). `docs/REAL_DATA_ACQUISITION_REPORT.md`.
- Consequence: **no dataset passes the evidential gate**, so no ML can be trained for research.

## 2. No ML model, no deployment, no real explanation

- `models = 0`, `experiments = 0`, `ml.trained = false`, `evidential_models = 0`.
- Deployment refuses non-evidential versions (`NON_EVIDENTIAL_MODEL`).
- SHAP explainability has no artifact to explain and correctly returns refusal.
- The decision module is rule+physics; `ml_result` is `null` by design until a genuine model deploys.

## 3. Prediction evidence is demonstration-only

- All comparison metrics are n = 1 per comparison, computed from synthetic demonstration values.
- Coverage 50%: the older smoke-check outcome lacks post-event salinity. MAE and bias are equal by
  construction (single observation); they carry no uncertainty bounds.

## 4. Simulation is a first-order dilution model

- Rainfall is a single instantaneous depth; no evaporation, no diurnal cycle, no stratification,
  no gate/flow dynamics, no density gradient (1 kg/L assumption). Stated in every response.

## 5. Not yet implemented (honest scope)

- **Authentication/authorisation** (Module 1): the API is OPEN ACCESS; `actor` is recorded but never
  verified. A deployed research instrument would need real auth before multi-user field operation.
- **Sensor gateway** (501): no hardware ingestion; observations are manually entered or API-inserted.
- **Aggregated dashboard storage** (Module 13 core) and **anonymised research export** (Module 18)
  remain stubs. Per-domain exports (datasets/evaluation/experiments/validation) work.
- **Weather integration**: `GET /api/weather/providers → registered: []`; forecasts are manual.

## 6. Engineering notes

- No git history exists at audit time (no commits); provenance is filesystem + live-state based.
- Rate limiting exists (tested) but no token-based throttling; deployment relies on host firewall in
  this single-machine configuration.
- The health fix this stage was verified on a fresh worker after `--reload`; a long-lived worker that
  entered the bad state needed a restart to clear it.

## 7. What would remove each limitation (paths, not promises)

1. A genuine field campaign per `docs/FIELD_COLLECTION_TEMPLATE.md` producing campaign-provenanced
   brine salinity/depth/rainfall observations → dataset passes the gate.
2. Model trained on that gated dataset, evaluated on held-out rows, registered → deployable.
3. More outcomes (genuine) → real comparison metrics with meaningful n.
4. Auth + sensor gateway + weather-API wiring → operational multi-user field deployment.