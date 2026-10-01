# SaltLens Research Evidence Directory

This directory is the **research evidence** deliverable of Stage 8. Everything here is
reproduced from the live system and the repository state of `2026-09-30`; nothing is invented or
padded. Files must not be edited to strengthen a claim: where evidence does not exist, the file says
so and points to the gate that refuses.

| File | What it holds |
|------|---------------|
| `architecture.md` | Actual system architecture (modules, store, boundaries, honesty model) |
| `equations_and_assumptions.md` | The transparent equations and the assumptions they carry |
| `dataset_provenance.md` | Data origin, licences, DOIs, import status |
| `methodology.md` | Workflow and guardrails (provenance, evidence gates, validation protocol) |
| `reproducibility.md` | Exact commands to reproduce the audit, tests and demonstration |
| `tests_report.md` | Genuine test reports for this stage |
| `prediction_evidence.md` | Actual prediction comparisons (demonstration-only; n = 1) |
| `explainability.md` | Legitimate explainability state (rule-based today; SHAP-refusing until a real model) |
| `labelled_demonstration.md` | The end-to-end workflow demonstration, explicitly labelled |
| `limitations.md` | Outstanding limitations, stated plainly |

## Standalone summary (highest-level evidence statement)

- **Field data: none.** Zero field-collected observations exist in the cluster. Every stored
  observation, weather record and outcome is labelled `demonstration` (or `smoke-check`, also
  demonstration).
- **ML model: none.** `evidential_models = 0`; no dataset passed the evidential gate; no model
  artifact exists; no training metric from field data can be produced.
- **Deployment: none.** The deployment gate refuses models without genuine evidence
  (`docs/ML_DEPLOYMENT_REPORT.md`).
- **Predictions that do exist** are (a) mass-balance arithmetic (checkable by hand) and (b) two
  demonstration events yielding one comparable pair per comparison. Reported in
  `prediction_evidence.md` with n, bias and signed error, never with a claim of validation.

## Provenance statement (who/what/where/when)

- System: SaltLens DSS, modules 1–19 as built on the working tree of 2026-09-30.
- Backend: FastAPI + uvicorn `--reload`, store MongoDB Atlas (`saltlens`).
- Frontend: Next.js, client-rendered dashboard.
- All counts verified by running the commands in `reproducibility.md` on 2026-09-30.