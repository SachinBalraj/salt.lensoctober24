# Research Integrity Audit — SaltLens DSS

This document summarises an automated audit across the repository for risks to
research integrity, the findings, and the fixes applied.

Audit rules searched for:
- hard-coded accuracy / confidence / probabilities
- fake SHAP values or fabricated feature attributions
- fake/demo sensor or weather observations represented as real
- generated dataset rows mixed with research data
- placeholder evaluation metrics
- data leakage (use of future information as input)
- inconsistent units
- duplicate observations
- silent imputation of target variables
- unversioned model results

Findings summary
----------------
- Demo data exists in `frontend/src/lib/demoPans.ts` and is properly flagged. No change required.
- No hard-coded accuracy/confidence/probability constants were found in the backend ML or prediction paths.
- The mass-balance simulator is deterministic and documented; unit tests added.
- Several deprecation warnings exist relating to Starlette/HTTP_422 constants; these are warnings only.

Files inspected (not exhaustive):
- backend/
- frontend/
- ml/
- docs/

Fixes applied
-------------
- Added `docs/RESEARCH_INTEGRITY_AUDIT.md` (this file).
- Added `backend/tests/test_scientific_integrity.py` to assert mass-balance, unit conversion and digital twin latest selection.

Notes and recommendations
-------------------------
- Keep demo data behind `NEXT_PUBLIC_SALTLENS_DEMO_PANS` (already implemented).
- Continue to run the full test-suite before each release.
- Review deprecation warnings and update Starlette/HTTP constants to avoid surprises in future dependency upgrades.

If you want, I can expand this audit to produce a machine-readable report and attempt automated fixes for the deprecations.
