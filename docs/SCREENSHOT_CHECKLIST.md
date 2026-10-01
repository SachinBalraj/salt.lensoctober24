# SaltLens DSS — Screenshot Checklist

Covers every implemented module. Each row gives the surface (UI page or API), the exact URL or
command, and the **minimum evidence the frame must show** so the capture proves the claim.
Two claim classes appear throughout:
- **[E1] Verified software functionality** — the module behaves as specified (tests pass, gates refuse).
- **[E3] Demonstration results** — explicitly labelled worked-example values (never real measurements).

[E2] (physics arithmetic), [E4] (genuine field data) and [E5] (trained ML results) have **no
screenshots**: E2 is verified by hand-checking the equations (see
`docs/evidence/equations_and_assumptions.md`); E4 and E5 are absent by design and must **not** be
screenshotted as if they existed.

Prep: servers running, `kzn-2026-demo9` registered with an observation + forecast + outcome.

## A. Browser screenshots (frontend UI)

| # | Module | Surface | URL | Frame must show |
|---|--------|---------|-----|-----------------|
| 1 | Health / meta | Home | `/` | Health card "connected", module map, "No research results are fabricated" banner [E1] |
| 2 | 02 Pans | Registry list | `/pans` | Pan cards incl. "Demo Pan Delta-9" (`kzn-2026-demo9`) [E1/E3] |
| 3 | 02 Pans | Register form | `/pans/new` | Form fields (area, normal depth, target salinity); client validation [E1] |
| 4 | 02 Pans | Pan detail | `/pans/kzn-2026-demo9` | Location text "Demonstration site (worked example, not a field site)", thresholds [E1/E3] |
| 5 | 02 Pans | Edit | `/pans/kzn-2026-demo9/edit` | Edit form loads/saves [E1] |
| 6 | 03/04/05/06/12/14 | Dashboard evidence | `/dashboard` | Condition (278.5 g/L, 18.2 cm), Weather (35 mm), Twin state `provenance: observed` [E1/E3] |
| 7 | 06 | Simulator | `/dashboard` (SimulationCard) | Rainfall sweep curve with forecast-depth marker; conservation note [E2/E3] |
| 8 | 06 | Demonstration sim | `/dashboard` (DemonstrationSimulationCard) | Labelled demo card, post-rain `233.58 g/L` [E3] |
| 9 | 12 | Decision | `/dashboard` (RecommendationCard) | TRANSFER BRINE, `basis: physics_and_rules`, `ml_result: null`, rule hash `06a32ee5…4d7b` [E1/E3] |
| 10 | 14 | Feedback | `/dashboard` (DecisionPanel) | Recorded decision; vocabulary buttons (HARVEST NOW/TRANSFER BRINE/PROTECT-DRAIN/WAIT AND RECHECK) [E1] |
| 11 | 12/14/15 | Trace | `/dashboard` (TraceCard) | advice → decision → outcome chain with IDs [E1/E3] |
| 12 | 16 | Validation | `/predicted-vs-actual` | Chart with n = 1, coverage 50 %, forecast +3 mm, salinity −34.345 g/L — demo-labelled [E1/E3] |
| 13 | 16 | Research validation | `/research-validation` | Overview page; comparison sections with demo-only note [E1] |
| 14 | 08/09 | ML refusal | `/dashboard` (MlRiskCard) | "Predict risk" → refusal (no trained model) — NOT a prediction [E1] |
| 15 | 11 | Explain refusal | `/dashboard` (ExplainabilityCard) | SHAP unavailable (no artifact) refusal [E1] |
| 16 | 09 | Evaluation | `/evaluation` | Empty/refusal state for no model [E1] |
| 17 | 19 | Experiments | `/experiments` | Empty state (0 experiments) [E1] |

## B. API / terminal captures (modules without a dedicated page)

| # | Module | Command | Frame must show |
|---|--------|---------|-----------------|
| 18 | Meta | `GET /openapi.json` | 91 paths; 21 `/api/*` prefixes [E1] |
| 19 | Health | `curl -s localhost:8000/api/health` | `database_health.indexes_ready: true`; `"database":"connected"` [E1] |
| 20 | 1 Auth | `GET /api/auth/` | `status: "not_implemented"` (OPEN ACCESS stated) [E1] |
| 21 | 16 Export | `GET /api/validation/export?comparison=forecast_vs_actual_rainfall&output_format=csv` | CSV header `outcome_id,pan_id,...,signed_error` [E1] |
| 22 | 16 pairs | `GET /api/validation/pairs?comparison=simulated_vs_actual_salinity` | `predicted 236.8551 / actual 271.2 / error −34.34486`, other row `compared: false` + reason [E1/E3] |
| 23 | 15 Outcomes | `GET /api/outcomes/` | both rows `is_demonstration: true` [E3] |
| 24 | 3 Observations | `GET /api/observations/` | rows with `data_source: demonstration` [E3] |
| 25 | 8 ML state | `GET /api/ml/` | `trained: false`, `evidential_models: 0` [E1] |
| 26 | 17 Models | `GET /api/models/` | empty registry; deployment refusal code `non_evidential_model` logic present [E1] |
| 27 | 14 Feedback validation | `POST /api/feedback/` (invalid action) | `422` code (closed vocabulary) [E1] |
| 28 | 3 Sensor gateway | `POST /api/observations/ingest/sensor` | `501` "not implemented" [E1] |
| 29 | 4 Weather | `GET /api/weather/providers` | `registered: []` [E1] |
| 30 | 7 Datasets | `GET /api/datasets/` | dataset registry viewing; 0 importable real datasets context [E1] |

## C. Evidence pages (screenshot for the appendix, not "proof of ML")

| # | File | Frame must show |
|---|------|-----------------|
| 31 | `docs/evidence/tests_report.md` | 1086 backend + 816 frontend + lint clean |
| 32 | `docs/evidence/prediction_evidence.md` | n = 1, demo-only label, MAE = bias = 34.345 |
| 33 | `docs/evidence/limitations.md` | no-field-data / no-ML / no-deploy register |

## Anti-patterns to avoid in captures

- Do **not** caption any demo value as a measurement.
- Do **not** include a prediction, algorithm card or SHAP field that is actually a refusal.
- Do **not** screenshot a chart with fewer than 1 demonstration pair and caption it as validation.
- Every E3 frame must contain the word "Demonstration" or a `data_source/source/is_demonstration:true`
  marker within the same viewport.