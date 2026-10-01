# IEEE Research Readiness Review — SaltLens DSS

This report reviews the repository as a basis for an academic research paper. It
classifies components and lists remaining experiments and data collection needed
before strong scientific claims can be made.

Summary classification
----------------------
- IMPLEMENTED AND VERIFIED
  - Backend API core, pan registration, observations, weather ingestion, simulator
  - Mass-balance simulator: equations implemented, unit-tested (see tests)
  - Digital twin assembly: implemented and tested for latest-observation selection
  - ML training pipeline: implemented with sufficiency checks and reproducible
    records (requires real labelled data to run)
  - SHAP explainability hooks and storage
  - Frontend: pages and components for the workflow; build and tests pass

- IMPLEMENTED BUT NOT YET VALIDATED WITH FIELD DATA
  - Full ML model evaluation against real field-collected datasets
  - Decision-support rules validated in operational trials
  - Prediction vs actual outcome validation across multiple pans and events

- PLANNED / NOT IMPLEMENTED
  - Live sensor gateway integration (architecture exists, no hardware connected)
  - Long-term field experiments and multi-site trials

Key verifications performed
--------------------------
- Backend unit and integration tests: all green (`pytest` run in backend).
- Frontend build, lint and unit tests: green (`npm run build`, `npm run test`).
- Mass-balance simulator: deterministic tests verifying volumes, salinity, dilution.
- Digital twin assembly: tests ensure latest observations are selected correctly.
- Research integrity audit: `docs/RESEARCH_INTEGRITY_AUDIT.md` added and demo
  data isolated behind `NEXT_PUBLIC_SALTLENS_DEMO_PANS`.

Required real-world experiments before research claims
----------------------------------------------------
1. Collect time-stamped paired readings (pre- and post-event) across multiple
   pans for several rainfall events to validate simulator accuracy and ML models.
2. Run operational decision trials to record operator decisions and outcomes.
3. Produce several independent training/evaluation runs with held-out pans to
   validate model generalisation.
4. Produce an external replication by a separate team using the reproducibility
   manifest the project records for each experiment.

Reproducibility notes
---------------------
- Every model/training run records dataset hash, split, seed, library versions and
  artifact path. This supports reproducibility once data are available.
- Demo data is separated and cannot be written to the research DB.

Tests run
---------
- Backend: `pytest` — all tests passed.
- Frontend: `npm run build`, `eslint`, `npm run test` (Vitest) — all tests passed.

Known limitations
-----------------
- No live sensor integration; sensor data must be imported manually or via API.
- No long-term field validation performed; ML components require labelled
  outcomes from actual events to be validated.
- Deprecation warnings exist (Starlette HTTP_422 constant). These are not
  correctness issues but should be addressed before production deployment.

How to reproduce this review
----------------------------
1. Create a Python virtual environment in `backend/` and install dev deps:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
cd backend
pytest -q
```

2. Frontend:

```bash
cd frontend
npm install
npm run build
npm run lint
npm run test
```

If you want, I can expand each section with file-by-file notes, automated
fixes for deprecation warnings, and a checklist for operational field trials.
