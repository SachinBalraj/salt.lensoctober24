# SaltLens — Labelled Demonstration (worked example)

This is an **explicitly labelled demonstration**, not a field trial. The pan's location field is
literally `"Demonstration site (worked example, not a field site)"`. Every datum carries a
demonstration marker. No value here describes a real pan or a real weather event.

## Demonstration pan: `kzn-2026-demo9` — "Demo Pan Delta-9"

| Field | Value |
|-------|-------|
| area_m2 | 12000 |
| normal_depth_cm | 16 |
| target_harvest_salinity_g_l | 290 |
| flood_depth_cm threshold | 50 |
| dilution_salinity_g_l threshold | 260 |
| critical_rainfall_24h_mm threshold | 90 |
| target_flow_cm | 1.5 |

## Workflow transcript (10 steps, all against the live backend)

| Step | Call | Result |
|------|------|--------|
| 1 | `POST /api/pans/` register demo pan | **201** `id=6abc94a9197d6d1aee72581c` |
| 2 | `POST /api/observations/` (278.5 g/L, 18.2 cm, `data_source: demonstration`) | **201** `obs-d3e4e4c610844c708c7d5dbab9ea9e89` |
| 3 | `POST /api/weather/` forecast 35 mm, 70 %, `source: demonstration` | **201** `wx-da06a4ed2c954fccbdbf25caa5bc4277` |
| 4 | `POST /api/twin/kzn-2026-demo9/refresh` | **201** twin complete, provenance `observed`, version 0.1.0 |
| 5 | `POST /api/simulator/mass-balance` (35 mm) + `rainfall-scenarios` {0,10,35,60} | **200** → 233.58 g/L; scenario list returned |
| 6 | `POST /api/decisions/advise` | **200** TRANSFER BRINE, `basis=physics_and_rules`, `ml_result=null`, rule v2 hash `06a32ee5…4d7b`, notice says DEMONSTRATION ONLY |
| 7 | `POST /api/feedback/` (`actual_action: "monitor"`) | **422** — closed vocabulary enforced |
| 7b | `POST /api/feedback/` (`actual_action: "WAIT AND RECHECK"`) | **201** `decision-8485ff39ab5b415bb8e0c07f633d30a0` |
| 8 | `POST /api/outcomes/` (forecast 35 / actual 32, post 271.2 g/L, `is_demonstration: true`) | **201** `outcome-c20ce3da5dec441eaa5db9cdb55d58dd` |
| 9 | `GET /api/outcomes/comparison` + `GET /api/validation/overview` | **200** n_compared=1, coverage 50 %, MAE 34.345 g/L, bias −34.345 |
| 10 | `GET /api/dashboard/` + frontend `/dashboard` | dashboard served; defeats of client render are in the frontend slice |

## What the demonstration proves

1. Every module in the operator loop (2,3,4,5,6,12,14,15,16) answers 200/201 and preserves
   demonstration labels.
2. Honest refusals work: the 422 on step 7 is the closed action vocabulary enforcing itself; the
   same mechanism that would refuse a bad field measurement.
3. The ML-dependent steps (8,9,11,17) refuse or stay `null` rather than invent numbers —
   `ml_result=null`, `evidential_models=0`.
4. Comparison/validation arithmetic produces real, auditable pairs — on demo data only.

## What it does NOT prove

- Any claim about real-pan accuracy or the simulator's skill (n = 1, synthetic).
- Any ML capability (no model exists).
- Any scientific conclusion about salt-pan management.

## Re-running

See `docs/STAGE8_DEMO_GUIDE.md` for the ordered request bodies with expected status codes.