# SaltLens — Stage 8 Final Report

- Date: 2026-09-30
- Scope: full system verification, research audit, demonstration, evidence pack, IEEE manuscript draft.
- Verdict inherited unchanged: **ML NOT READY** (Stage 6), **ML NOT DEPLOYED** (Stage 7). Stage 8
  does not alter them and does not claim validation.

---

## 1. What was delivered (all paths are in the repository)

| Deliverable | Path |
|-------------|------|
| Technical audit (§A) + scientific audit (§C) | `docs/STAGE8_TECHNICAL_AUDIT.md` |
| Demonstration guide (§B) | `docs/STAGE8_DEMO_GUIDE.md` |
| Research evidence directory (§E) | `docs/evidence/` (12 files) |
| Reproducibility instructions (in evidence pack) | `docs/evidence/reproducibility.md` |
| IEEE manuscript draft (§F) | `docs/SaltLens_DSS_IEEE_manuscript.md` |
| Test report (in evidence pack) | `docs/evidence/tests_report.md` |
| Outstanding limitations (in evidence pack) | `docs/evidence/limitations.md` |

## 2. Module status (read live from each router)

Implemented: 2 pans · 3 observations · 4 weather · 5 twin · 6 simulator · 7 datasets ·
8 ml-training · 9 evaluation · 10 risk · 11 explain · 12 decisions · 14 feedback ·
15 outcomes · 16 validation · 17 model registry · 19 experiments.
Not implemented (honestly reported, not silent): 1 auth (OPEN ACCESS API) · 13 dashboard core
(UI aggregates live) · 18 research export (per-domain export endpoints do work).

## 3. Demonstration result

Full operator loop passed end-to-end on the live stack using the **explicitly labelled** pan
`kzn-2026-demo9` ("Demonstration site (worked example, not a field site)"): register (201) →
observation `data_source: demonstration` (201) → forecast `source: demonstration` (201) → twin
refresh (201, complete) → mass balance **233.58 g/L** (200) → advice **TRANSFER BRINE**,
`basis: physics_and_rules`, `ml_result: null` (200) → feedback (422 on out-of-vocabulary action,
then 201) → outcome `is_demonstration: true` (201) → comparison/validation (200) → dashboard (200).

## 4. Test results (final)

- Backend: **1086 passed / 1086 collected**.
- Frontend: **39 files, 816 tests passed**; `tsc --noEmit` clean; ESLint clean.
- `ruff check app tests`: **All checks passed** (7 issues found earlier this stage, fixed).

## 5. Database verification (live)

- pans **2** · observations **3** · weather **3** · outcomes **2** — every row demonstration-labelled.
- models **0** · experiments **0** · `ml.trained: false` · `evidential_models: 0`; no artifacts under
  `ml/models/`.
- Health now truthful: `connected: true, indexes_ready: true, error: null` (was mislabelling an
  index-provisioning failure as "MongoDB is unreachable." — fixed, see §7).

## 6. Dataset readiness and scientific validation status

- Field data: **none**. 486 553 normalised public-hydrology rows exist but are not importable as
  salt-pan training data (semantics mismatch, Stage 5). No field campaign has run.
- Prediction evidence: the only comparisons are **demonstration-only, n = 1** (forecast error +3 mm;
  simulated salinity error −34.345 g/L from actual rainfall, −37.62 g/L from forecast; MAE=bias
  34.345 g/L; coverage 50 %). Explicitly not simulator-skill evidence.
- ML/deployment: no genuine dataset ⇒ no model ⇒ no evaluation ⇒ no deploy. All gates refuse with
  machine-readable codes; no metric, DOI, citation or SHAP value has been invented.

## 7. Changes made this stage (§D)

1. `app/db/mongo.py` — health accuracy fix: logged real cause, `_last_error` = "MongoDB is connected
   but indexes could not be provisioned"; retry path preserved.
2. `tests/test_database.py` — regression test for (1); 14/14 pass.
3. `ruff` cleanup of 7 pre-existing/stage issues (import sorts, annotations, mutable default,
   one deliberate `# noqa: BLE001`).
4. Frontend and module behaviour untouched.

## 8. Outstanding issues (full register: `docs/evidence/limitations.md`)

- No auth/authorisation (OPEN ACCESS); `actor` unverified.
- No sensor gateway (501), no weather-API wiring (`registered: []`).
- Dashboard aggregation core and anonymised research export are stubs.
- Simulator is first-order (instant rainfall, no evaporation/flow dynamics).
- No field data, no ML, no deployment — the scientific-validity gap.
- No git commits existed at audit time.

## 9. How to run everything again

```bash
cp backend/.env.example backend/.env            # fill MONGODB_URI (template has real key names)
scripts/dev.sh                                   # backend :8000; start frontend separately
cd backend && .venv/bin/python -m pytest -q     # 1086 pass
cd backend && .venv/bin/ruff check app tests    # clean
cd frontend && npx vitest run && npx tsc --noEmit && npx eslint src/
```
Full commands and the demonstration transcript are in `docs/STAGE8_DEMO_GUIDE.md` and
`docs/evidence/reproducibility.md`.

---

_Preparation of this report did not fabricate evidence. Every number above is re-derivable from the
live system via the documented commands, or is stated as an absence._