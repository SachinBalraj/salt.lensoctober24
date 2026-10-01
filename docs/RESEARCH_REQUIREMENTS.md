# SaltLens — Outstanding Research Requirements

Register of the **exact experimental evidence still needed** before any claim of AI model
performance or field validation can be made for SaltLens. Status of every line today: **ABSENT — no
field campaign has run, no genuine dataset exists, no model is trained or deployed.** Nothing here
is a promise of results; it is the acceptance contract that the system's evidence gates already
enforce.

Reference gates the requirements map to: `docs/evidence/methodology.md` (provenance model),
`docs/evidence/limitations.md` (current state), `docs/evidence/dataset_provenance.md` (no field data).

---

## R-A. Genuine field data acquisition (precondition for everything else)

| Req | Claim gates | Evidence needed | Protocol | Acceptance mechanism | Status |
|-----|-------------|-----------------|----------|----------------------|--------|
| R-A1 | "The system holds genuine pan observations" | `N_pan` pans × `N_visit` visits with brine depth (cm), salinity (g/L), brine/ambient temp, humidity; ≥3 sampling points per pan per visit | Field sheet (`docs/FIELD_DATA_COLLECTION_SHEET.md`); calibrated instruments same day; photos; per-campaign provenance in `data/templates/README.md` | Ingestion marks rows `MANUAL` (GENUINE track); `GET /api/observations/` shows `data_source: manual` | ABSENT |
| R-A2 | "The system holds genuine rainfall/forecast pairing" | Actual rainfall mm + capture time per event; forecast mm with `forecast_for`; source comment | Rain gauge at site; daily log (sheet page 5/6); NWP provider field | `GET /api/weather/` shows `source: manual`; `weather.providers.count > 0` when wired | ABSENT |
| R-A3 | "The system holds genuine event outcomes" | `event_id`, pre/post salinity g/L AND pre/post depth cm, actual rainfall, `operator_action` within the 4-action vocabulary, `harvest_outcome` | Event log (sheet page 6); post-event measurement within declared window | `GET /api/outcomes/` shows `is_demonstration: false`; validation `compared: true` rows | ABSENT |
| R-A4 | "Dataset provenance is complete" | Every row attributable to a campaign + instrument + collector with calibration record | Sheet pages 1 + 4; campaign README | Evidence verdict for dataset = `GENUINE` in `app/ml/provenance.py` | ABSENT |

## R-B. Field validation of the mass-balance simulator

| Req | Claim gate | Evidence needed | Protocol | Acceptance mechanism | Status |
|-----|------------|-----------------|----------|----------------------|--------|
| R-B1 | "Simulator predicts post-event salinity with useful accuracy" | **≥ 30 genuine events across ≥ 3 pans**, each with pre/post salinity, actual rainfall, depths within the model's assumption window (single rain event, no net inflow/outflow) | B.1 requires R-A1–A3 complete; simulate each event; collect predicted vs actual pairs | `GET /api/validation/pairs?comparison=simulated_vs_actual_salinity` all `compared: true`; report MAE, RMSE, bias **with n and confidence intervals** | ABSENT |
| R-B2 | "Forecast-based simulation is useful for decisions" | Same events with forecast rainfall recorded to compare forecast-driven vs actual-driven simulation error | Record `forecast_rainfall_mm` in the outcome (sheet page 6) | `forecast_simulated_vs_actual_salinity` comparison populated | ABSENT |
| R-B3 | "Simulator operating envelope is characterised" | Test events outside assumptions (multi-day events, net flows, harvest between samples) to bound validity | Deliberate field probing; note in event log | Documented exclusion reasons in `comparison` rows, not silent drops | ABSENT |

## R-C. Trained-ML results (would give E5 evidence)

| Req | Claim gate | Evidence needed | Protocol | Acceptance mechanism | Status |
|-----|------------|-----------------|----------|----------------------|--------|
| R-C1 | "A model was trained on genuine data" | A dataset that returns `GENUINE` verdict with enough labelled rows; targets available: `harvest_outcome`, `operational_risk`, `post_rain_salinity_band` | R-C1 requires R-A complete; `POST /api/ml/train` records dataset hash + split | `GET /api/ml/` shows `trained: true`, `evidential_models: 1`; artifact under `ml/models/` | ABSENT |
| R-C2 | "Machine is evaluated on held-out genuine rows only" | Evaluation run that re-reads the stored evidence; `chronological` or `per_pan_chronological` split to avoid leakage | Module 9 canonical scorer on the fixed hold-out | `GET /api/evaluation/{model_id}` returns metrics with the split + dataset hash attached to the version record | ABSENT |
| R-C3 | "Metrics are not memorisation artefacts" | Report macro-F1, precision/recall, PR-AUC; Random Forest must not be auto-selected (system rule) | Use documented candidate set (LR/DT/RF/GB) | Experiment manifest lists each candidate's held-out scores; RF present but unselected | ABSENT |
| R-C4 | "The model is safely deployable/retirable" | Deployment validate+deploy passes re-reading the version's genuine evidence | `POST /api/models/{id}/validate`, then `/deploy` | Only genuine-evidence versions pass (`NON_EVIDENTIAL_MODEL` otherwise); deploy/retire history recorded | ABSENT |
| R-C5 | "Predictions are explainable" | A registered artifact exists; SHAP attribution reproducible and guardrail-tested on unseen rows | `GET /api/explain/…` from the artifact | Explanation returned with feature attribution; `test_explain.py` behaviours hold on the real model | ABSENT |

## R-D. Operational-validity claims

| Req | Claim gate | Evidence needed | Protocol | Acceptance mechanism | Status |
|-----|------------|-----------------|----------|----------------------|--------|
| R-D1 | "Rule/ML recommendations align with outcomes" | Good outcomes where operator action and outcome are both recorded (R-A3) | Record decisions against recommendations (`POST /api/feedback/`) with valid actions | `recommended_vs_operator_action` validation section populated | ABSENT |
| R-D2 | "Risk predictions are calibrated" | Risk-labelled genuine records (operational_risk target) with decisions and outcomes | Risk module scoring on held-out genuine rows | `predicted_risk_vs_outcome` section with calibration, not just scores | ABSENT |
| R-D3 | "Forecast reliability is established" | Genuine forecast vs actual on ≥ 90 days (or ≥ 30 events); Brier score for rain-probability, MAE for mm | Daily weather log (sheet page 5) + provider history | `forecast_vs_actual_rainfall` comparison populated with n ≥ 30 | ABSENT |

## R-E. Rigor requirements (quality of every claim above)

| Req | Claim gate | Evidence needed | Protocol | Acceptance mechanism | Status |
|-----|------------|-----------------|----------|----------------------|--------|
| R-E1 | "Uncertainty is reported" | Instrument error from calibration log; assumption sensitivity (evaporation ignored, instantaneous rainfall, ρ=1 g/cm³) | Re-simulate under assumption bounds | Every claimed metric shows n, CIs/error bars, and assumption caveat | ABSENT |
| R-E2 | "Results are reproducible" | Dataset hash, split seed, experiment manifest, model version + artifact hash, source code commit(s) | Git history must exist (repo currently has none); run per `docs/evidence/reproducibility.md` | Experiment manifest + model history endpoints return full records | ABSENT |
| R-E3 | "External validity is shown" | Independent hold-out: a second campaign/site never used for fitting | Keep one campaign's data back until evaluation day | `grouped_by_pan` / per-pan-chronological split statistics reported | ABSENT |

---

## The one-line summary

Every AI-performance and field-validation claim requires, at minimum, **R-A** (genuine,
provenanced, instrumented field records) and **R-E2** (versioned, reproducible research). Until
then the correct public statement remains: *"SaltLens is a verified, evidence-gated DSS; the operator
loop is demonstrated on labelled worked-example data; no model, field validation or ML performance
is claimed."*