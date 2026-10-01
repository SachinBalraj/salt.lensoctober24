# SaltLens — Architecture (as built, 2026-09-30)

## Component map

```
Operator UI (Next.js, :3000, client-rendered dashboard)
        │  HTTP
        ▼
FastAPI backend (:8000)  ── persisted via local MongoClient ──► MongoClient ──► MongoDB Atlas (saltlens)
        │
        ├── app/api/routers/         19 module routers (module numbers in tags)
        ├── app/services/            pans, observations, weather, twin, simulator, datasets,
        │                            ml (training), evaluation, risk, explain, decisions,
        │                            outcomes, validation, feedback, deployment, security
        ├── app/ml/                  provenance.py (evidence gate), training pipeline CLI,
        │                            explainability (SHAP from artifact)
        └── ml/                      ingestion CLI, training/evaluation packages (offscreen at :8000)
```

## Module inventory (live status read from each router)

| Module | Path | Status |
|--------|------|--------|
| 1 Authentication | `/api/auth` | not_implemented — OPEN ACCESS API, no route requires a token |
| 2 Salt pans | `/api/pans` | implemented |
| 3 Observations | `/api/observations` | implemented |
| 4 Weather | `/api/weather` | implemented |
| 5 Digital twin | `/api/twin` | implemented |
| 6 Simulator | `/api/simulator` | implemented |
| 7 Datasets | `/api/datasets` | implemented (+ CLI ingestion/normalisation) |
| 8 ML training | `/api/ml` | implemented; refuses on non-evidential datasets |
| 9 Evaluation | `/api/evaluation` | implemented; refuses on non-evidential models |
| 10 Risk criteria | `/api/risk` | implemented; refuses without evidential model |
| 11 Explainability | `/api/explain` | implemented; SHAP computed only from a registered artifact |
| 12 Decisions | `/api/decisions` | implemented; rule+physics now, ML when a model exists |
| 13 Dashboard | `/api/dashboard` | not_implemented (core); UI aggregates the other modules |
| 14 Feedback | `/api/feedback` | implemented; closed action vocabulary |
| 15 Outcomes | `/api/outcomes` | implemented; `is_demonstration` flag |
| 16 Validation | `/api/validation` | implemented; comparison exports (csv/json) |
| 17 Model registry | `/api/models` | implemented; evidence re-read at validate/deploy time |
| 18 Research export | `/api/export` | not_implemented stub; per-domain export endpoints exist & work |
| 19 Experiments | `/api/experiments` | implemented |

## Key architectural properties

1. **Evidence gate is centralised**, not repeated per module: `app/ml/provenance.py` computes a
   provenance verdict (GENUINE / DEMONSTRATION / MIXED / UNKNOWN) that fails closed, and training
   (`app/services/ml.py`), evaluation (`app/services/evaluation.py`) and deployment
   (`app/services/deployment.py`) all re-read it at the moment of decision. Deployment re-reads the
   recorded evidence of the model version before validate/deploy (`# noqa`-free, covered by
   `test_deployment_api.py`).
2. **Demo data is structurally labelled**: observations carry `data_source`, weather carries
   `source`, outcomes carry `is_demonstration`. The research-integrity guard (`test_scientific_integrity.py`)
   asserts these markers survive the write path.
3. **Deployment gate** (`NON_EVIDENTIAL_MODEL`) refuses any version whose stored evidence is not
   genuine — ML is currently un-deployable by construction because no genuine dataset exists
   (`docs/ML_DEPLOYMENT_REPORT.md`).
4. **Transparency over black boxes**: the mass-balance simulator is pure arithmetic with stated
   assumptions; the decision module today is rules + physics; ML, when it arrives, must earn its
   position through the evidence gate.
5. **Health is self-reported and now accurate**: `GET /api/health` exposes `connected`, `indexes_ready`
   and a truthful `error`. An index-provisioning failure is reported as itself, not as a connection
   failure (Stage 8 fix, `app/db/mongo.py`).

## Boundaries that exist on purpose

- No sensor gateway, no weather-API key, no auth — these are honestly reported not_implemented / 501, never silently absorbed.
- All ML-facing endpoints refuse (409/500-with-code) rather than return placeholder metrics.