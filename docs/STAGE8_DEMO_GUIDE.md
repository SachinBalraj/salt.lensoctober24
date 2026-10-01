# SaltLens — Stage 8 Demonstration Guide

This guide replays the labelled end-to-end demonstration. **Everything here is a worked example,
not a field trial.** Expected status codes are included so the guide doubles as a checklist.

Requirements: backend on `:8000` (`scripts/dev.sh`), `MONGODB_URI` in `backend/.env`, no prior
`kzn-2026-demo9` row (delete/re-register gives a fresh pan each run).

## Step 1 — register the demonstration pan

```bash
curl -s http://localhost:8000/api/pans/ -X POST -H 'Content-Type: application/json' -d '{
  "pan_id": "kzn-2026-demo9",
  "pan_name": "Demo Pan Delta-9",
  "location": "Demonstration site (worked example, not a field site)",
  "area_m2": 12000, "normal_depth_cm": 16, "target_harvest_salinity_g_l": 290,
  "risk_thresholds": {"flood_depth_cm": 50, "dilution_salinity_g_l": 260,
                      "critical_rainfall_24h_mm": 90, "target_flow_cm": 1.5}
}'
```
**Expect `201`** (id like `6abc94a9197d6d1aee72581c`).

## Step 2 — record the observation (demonstration provenance)

```bash
curl -s http://localhost:8000/api/observations/ -X POST -H 'Content-Type: application/json' -d '{
  "pan_id": "kzn-2026-demo9", "timestamp": "2026-09-29T06:00:00Z",
  "salinity_g_l": 278.5, "brine_depth_cm": 18.2,
  "brine_temp_c": 31.0, "humidity_percent": 58, "ambient_temp_c": 26.0,
  "data_source": "demonstration"}'
```
**Expect `201`** (`obs-d3e4e4c610844c708c7d5dbab9ea9e89`).

## Step 3 — record the weather forecast (demonstration provenance)

```bash
curl -s http://localhost:8000/api/weather/ -X POST -H 'Content-Type: application/json' -d '{
  "pan_id": "kzn-2026-demo9", "forecast_for": "2026-09-29T18:00:00Z",
  "rainfall_mm": 35, "probability": 0.70, "source": "demonstration"}'
```
**Expect `201`** (`wx-da06a4ed2c954fccbdbf25caa5bc4277`).

## Step 4 — refresh the digital twin

```bash
curl -s http://localhost:8000/api/twin/kzn-2026-demo9/refresh -X POST
```
**Expect `201`** with `is_complete: true`, `missing_fields: []`,
`observation.provenance: observed`, `source: demonstration`.

## Step 5 — simulate dilution

```bash
curl -s http://localhost:8000/api/simulator/mass-balance -X POST -H 'Content-Type: application/json' \
  -d '{"area_m2": 12000, "initial_depth_m": 0.182, "initial_salinity_g_l": 278.5, "rainfall_mm": 35}'
curl -s 'http://localhost:8000/api/simulator/rainfall-scenarios?area_m2=12000&initial_depth_m=0.182&initial_salinity_g_l=278.5'
```
**Expect `200`**; post-rain salinity **233.5806… g/L** (verifiable by hand, see
`docs/evidence/equations_and_assumptions.md`).

## Step 6 — ask for advice

```bash
curl -s http://localhost:8000/api/decisions/advise -X POST -H 'Content-Type: application/json' \
  -d '{"pan_id": "kzn-2026-demo9"}'
```
**Expect `200`**: `recommendation: TRANSFER BRINE`, `basis: physics_and_rules`, `ml_result: null`,
rule v2 sha256 `06a32ee5…4d7b`, `notice` containing "DEMONSTRATION ONLY", and a
`recommendation_id` (e.g. `rec-f41de75246d04802b8aedacdaa22e515`).

## Step 7 — operator feedback (validated vocabulary)

```bash
# Deliberate error: 'monitor' is not in the closed vocabulary
curl -s http://localhost:8000/api/feedback/ -X POST -H 'Content-Type: application/json' \
  -d '{"recommendation_id": "rec-f41de75246d04802b8aedacdaa22e515", "decision": "accepted",
       "actual_action": "monitor", "operator_id": "op.stage8.demo"}'
```
**Expect `422`** (enum validation). Retry with a permitted action:

```bash
curl -s http://localhost:8000/api/feedback/ -X POST -H 'Content-Type: application/json' \
  -d '{"recommendation_id": "rec-f41de75246d04802b8aedacdaa22e515", "decision": "accepted",
       "actual_action": "WAIT AND RECHECK", "operator_id": "op.stage8.demo",
       "notes": "Demonstration acceptance to exercise the feedback ledger.",
       "timestamp": "2026-09-29T07:30:00Z"}'
```
**Expect `201`** (`decision-8485ff39ab5b415bb8e0c07f633d30a0`).

## Step 8 — record the outcome (is_demonstration)

```bash
curl -s http://localhost:8000/api/outcomes/ -X POST -H 'Content-Type: application/json' -d '{
  "pan_id": "kzn-2026-demo9", "event_id": "demo-event-2026-09-29",
  "forecast_rainfall_mm": 35.0, "actual_rainfall_mm": 32.0,
  "pre_event_salinity_g_l": 278.5, "post_event_salinity_g_l": 271.2,
  "pre_event_depth_cm": 18.2, "post_event_depth_cm": 21.6,
  "operator_action": "monitored only", "is_demonstration": true}'
```
**Expect `201`** (`outcome-c20ce3da5dec441eaa5db9cdb55d58dd`).

## Step 9 — validation and comparison

```bash
curl -s http://localhost:8000/api/outcomes/comparison | python3 -m json.tool
curl -s 'http://localhost:8000/api/validation/overview?pan_id=kzn-2026-demo9' | python3 -m json.tool
curl -s 'http://localhost:8000/api/validation/pairs?comparison=simulated_vs_actual_salinity'
curl -s 'http://localhost:8000/api/validation/export?comparison=forecast_vs_actual_rainfall&output_format=csv'
```
**Expect `200`** everywhere. Expected numbers with this transcript: rainfall error **+3 mm**;
salinity simulated-from-rainfall error **−34.345 g/L**; simulated-from-forecast error **−37.62 g/L**;
coverage **50%** (second outcome lacks post-event salinity); MAE = bias = **34.345 g/L** at n = 1.

## Step 10 — dashboard

```bash
curl -s http://localhost:8000/api/dashboard/ | python3 -m json.tool   # module status (aggregation core is a stub)
curl -s -o /dev/null -w '%{http_code} %{size_download}\n' http://localhost:3000/dashboard
```
Frontend `/dashboard` is client-rendered; expect HTTP 200 and hydration from the audited APIs.

## Checklist outcome

- All modules 2,3,4,5,6,12,14,15,16 respond 200/201 with demonstrations preserved.
- Module 8/9/11/17 refuse (or return `null`/`0`) — no ML invented.
- One 422 proves the closed authority boundary on feedback actions.

The transcript with identifiers is in `docs/evidence/labelled_demonstration.md`.