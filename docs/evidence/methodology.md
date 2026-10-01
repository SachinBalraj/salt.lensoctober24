# SaltLens — Methodology

This file states how evidence is produced, gated and recorded, so that a claim in this directory can
be traced to the mechanism that made it.

## 1. Provenance model (`app/ml/provenance.py`)

Every training dataset carries an evidence verdict computed from the provenance of its rows:

- `GENUINE` — rows are field-collected measurements with intact campaign provenance.
- `DEMONSTRATION` — every row is a labelled demonstration/smoke-check value.
- `MIXED` — a mixture of the above.
- `UNKNOWN` — provenance is missing or unreadable.

The gate **fails closed**: anything that is not provably `GENUINE` cannot train, evaluate, explain
or deploy a model for research conclusions.

## 2. Where the gate is enforced (not repeated per route)

- Training: `app/services/ml.py` — refuses non-evidential datasets before any fit.
- Evaluation: `app/services/evaluation.py` — refuses to score a non-evidential model.
- Deployment: `app/services/deployment.py` — at `validate`/`deploy` time re-reads the model version's
  stored evidence; a non-genuine version is refused with code `NON_EVIDENTIAL_MODEL`.
- Risk scoring: Module 10 refuses without an evidential model.
- Explainability: SHAP is computed only from a registered artifact; there is no artifact, so no
  SHAP output exists.

`ml.trained = false`, `evidential_models = 0`, `models = 0`, `experiments = 0` are read from live
state and simply reported.

## 3. Validation protocol

`GET /api/outcomes/comparison` and `GET /api/validation/*` build comparison pairs only where both
sides exist and are on the same semantics:

- Forecast rainfall vs recorded rainfall.
- Simulated salinity (from the rainfall that actually fell) vs recorded post-event salinity.
- Simulated salinity (from the forecast rainfall) vs recorded post-event salinity.

Non-comparable events (e.g. no post-event salinity recorded) are reported as `not_comparable`
alongside the comparable count — they are never dropped silently. All metrics are demo-only today
(`prediction_evidence.md`).

## 4. Stage discipline (how this directory itself was made)

- Evidence statements are written from live API reads and executed command output, dated 2026-09-30.
- Where a claim requires a number, the number appears in `reproducibility.md` as a command the reader
  can re-run.
- "Not implemented", "no data", "no model", "n = 1, demonstration" are written as themselves.
- No DOI, citation, experiment or metric is invented.