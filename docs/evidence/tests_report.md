# SaltLens — Genuine Test Report (Stage 8, 2026-09-30)

Reproducible via `docs/evidence/reproducibility.md`.

## Backend — pytest

**1086 collected, 1086 passed, exit 0**, `--tb=short -q`, no filters.

Module-scoped (969) mapped to their modules:

| Module | File | Tests |
|--------|------|-------|
| 2 Pans | `test_salt_pans_api.py` | 50 |
| 3 Observations | `test_observations_api.py` | 72 |
| 4 Weather | `test_weather_api.py` | 72 |
| 5 Twin | `test_digital_twin_api.py` | 40 |
| 6 Simulator | `test_simulator_api.py` + `test_mass_balance.py` | 31 + 29 |
| 7 Datasets | `test_datasets_api.py` + `test_ingestion_pipeline.py` + `test_normalize_sources.py` | 81 + 68 + 20 |
| 8 ML | `test_ml_api.py` + `test_ml_pipeline.py` | 31 + 73 |
| 9 Evaluation | `test_evaluation_api.py` + `test_ml_evaluation.py` + `test_evaluation_absence.py` | 39 + 45 + 14 |
| 10 Risk | `test_risk_definitions.py` + `test_risk_prediction.py` | 44 + 21 |
| 11 Explain | `test_explain.py` | 30 |
| 12 Decisions | `test_decisions.py` | 70 |
| 14 Feedback | `test_feedback.py` | 30 |
| 15 Outcomes | `test_outcomes_api.py` | 52 |
| 16 Validation | `test_validation_api.py` | 17 |
| 17 Models | `test_models.py` (+ `test_ml_api.py` above) | 19 + 31 |
| 19 Experiments | `test_experiment_api.py` | 21 |

Cross-cutting (117): `test_config.py` 4, `test_database.py` 14, `test_health.py` 7,
`test_indexes.py` 12, `test_ml_provenance.py` 17, `test_deployment_api.py` 35,
`test_security_guards.py` 19, `test_ratelimit.py` 4, `test_scientific_integrity.py` 5.

## Frontend — vitest / tsc / eslint

- Vitest: **39 files / 816 tests passed** (duration ~3.8 s).
- `tsc --noEmit`: exit 0, no diagnostics.
- `eslint src/`: exit 0, no diagnostics.

## Lint (backend)

- `ruff check app tests`: before Stage 8 → 7 errors; after Stage 8 fixes → **All checks passed**.
  Fixes were lint-only: import sorting, unused import, two `__aexit__` star-annotations, one
  `__aenter__ -> Self`, one mutable default (`weather=[]` → `None`), and one targeted
  `# noqa: BLE001` on the deliberate record-and-retry `except` in `app/db/mongo.py`.

## New tests added this stage

1. `test_an_index_provisioning_failure_is_not_reported_as_a_connection_failure`
   (`tests/test_database.py`) — asserts an index-provisioning PyMongoError is logged as itself and
   surfaced with the word `indexes`, never with the connection-unreachable sentence.

## Positive assertions baked into the suite (evidence-relevant)

- Deployment refuses non-evidential models at validate *and* deploy (`test_deployment_api.py`).
- A model trained on demo data is scored on held-out rows only, and its report is labelled demo
  (`test_ml_evaluation.py`, `test_ml_provenance.py`).
- Demo/smoke rows can never be promoted to field measurements (`test_scientific_integrity.py`).
- Simulator conserves salt to 1e-9 (`test_mass_balance.py`).