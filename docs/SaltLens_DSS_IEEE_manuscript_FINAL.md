# SaltLens DSS: An Evidence-Gated Digital Twin Decision Support System for Climate-Resilient Salt-Pan Management

**Final version for IEEE formatting — 2026-09-30**
_All numbers in this manuscript were re-verified against the live system, source, and test suites on
2026-09-30 before release. Every claim is traceable to `docs/evidence/`. **No field measurement,
accuracy value, citation or ML result is fabricated.** Where a class of evidence does not exist, the
paper says so and points to the gate that refuses it._

---

## Title page block (for IEEE template ingestion)

- **Title:** SaltLens DSS: An Evidence-Gated Digital Twin Decision Support System for Climate-Resilient Salt-Pan Management
- **Authors:** _[to be completed by the research team — no authorship is asserted in this repository file]_
- **Affiliations:** _[to be completed]_
- **Corresponding author / email:** _[to be completed]_
- **Keywords** (index terms):  digital twin · decision support;  evidence-gated machine learning;  salinity modelling;  brine mass balance;  field-integrity provenance

## Abstract

Climate variability threatens artisanal salt-pan brine operations that have no access to
instrumentation, forecasting or decision analytics. SaltLens DSS is a three-tier decision support
system built around a deliberate asymmetry: a fully functional and verified operator loop—pan
registration, brine observation, weather forecast, digital-twin assembly, rainfall-dilution
simulation, rule-and-physics recommendation, operator feedback, outcome recording and model-free
validation—together with an **evidence-gated** machine-learning layer that refuses to train,
evaluate, explain or deploy anything that is not provably genuine field data. On the reference
deployment the complete operator loop runs end-to-end on explicitly labelled demonstration data,
while the ML layer reports `evidential_models = 0` and every trained-model endpoint returns a
machine-readable refusal. No field measurements exist in the system, no model is trained or
deployed, and **no field validation or ML performance is claimed in this paper**; the contribution
is the verified architecture, its reproducible verification evidence, and a precise register of the
experimental data that would enable the intended scientific claims.

## I. Introduction

Salt pans are shallow engineered basins in which brine is concentrated by evaporation; a large
rainfall event dilutes the brine and can force an emergency harvest or brine transfer. Producers
without sensors and forecast tools typically decide by inspection. The value of a digital-twin DSS
is converting a pan's *recorded* brine state and a *forecast* rainfall into a *traceable,
auditable* recommendation.

This paper does not claim a new physical model or a winning classifier. Its contribution is the
**evidence discipline** that makes every future scientific claim verifiable:

1. a provenance model (GENUINE / DEMONSTRATION / MIXED / UNKNOWN) that fails closed;
2. enforcement of that model at train, evaluate, explain and deploy time, not as a report but as a
   refusal;
3. a first-order rainfall-dilution simulator whose assumptions and intermediate values travel with
   every response so its arithmetic is checkable by hand;
4. an end-to-end demonstration on data that is *structurally* labelled as a worked example—so
   demonstration numbers can never be mistaken for measurements;
5. an executable register of the exact evidence still required for any AI-performance or
   field-validation claim (`docs/RESEARCH_REQUIREMENTS.md`).

## II. System architecture (as built and verified)

### II.A Topology

- **Data plane:** MongoDB Atlas (database `saltlens`), accessed through an application MongoClient.
  `GET /api/health` reports `connected`, `indexes_ready` and an accurate `error`; on the reference
  deployment `connected: true`, `indexes_ready: true`, `error: null`.
- **Service plane:** a FastAPI backend exposing **91 OpenAPI endpoints across 21 `/api/*` prefixes
  implemented by 19 module routers** (auth, pans, observations, weather, digital twin, simulator,
  datasets, ml-training, evaluation, risk, explainability, decisions, dashboard, feedback, outcomes,
  validation, model registry, export, experiments), plus app-level `/api/health` and `/api/modules`.
  Every router declares its own status; modules that are not implemented report
  `not_implemented` rather than failing silently.
- **Presentation plane:** a Next.js operator UI whose dashboard is client-rendered over the same
  APIs, with an explicit "no fabricated research results" banner.

### II.B The evidence gate

`app/ml/provenance.py` classifies any dataset by the provenance of its rows
(GENUINE / DEMONSTRATION / MIXED / UNKNOWN) and fails closed. Training (`app/services/ml.py`),
evaluation (`app/services/evaluation.py`) and deployment (`app/services/deployment.py`) each
re-read provenance at the moment of decision. Deployment is the strongest actor: at validate or
deploy time it re-reads the model version's stored evidence and emits code `non_evidential_model`
for anything not genuine. The proof is in the tests: `test_deployment_api.py` wipes a trained
model's recorded evidence and shows that validate and deploy both refuse.

### II.C The twin and the simulator

The digital twin (Module 5) is a join of the pan, its observations and its weather—a join, not a
fitted model. The simulator (Module 6) is volume/salt conservation

```
V0 = A·D0             Vr = A·R            V1 = V0 + Vr
C1 = C0·V0/V1         dilution % = (C0 − C1)/C0 · 100
```

with a conservation check reported to 1e-9. Assumptions (instantaneous rainfall, uniform filling,
full mixing, pure-water precipitation, ρ = 1 g/cm³, no evaporation) are returned with every result.
The decision loop (Module 12) selects from a closed vocabulary—HARVEST NOW / TRANSFER BRINE /
PROTECT / DRAIN / WAIT AND RECHECK—using a versioned operator rule whose sha256 hash
(`06a32ee5…4d7b`, verified live) travels with every recommendation, alongside `basis:
physics_and_rules` and `ml_result: null`.

## III. Verification method

Three independent checks were run on 2026-09-30:

1. **Full test suites** — backend `pytest`: **1086 collected / 1086 passed**; frontend Vitest:
   **39 files / 816 tests passed**; `tsc --noEmit` and `eslint src/` clean; `ruff check app tests`:
   **All checks passed**.
2. **Live-state reads** — health, module statuses, storage counts, the deployed rule hash, and every
   validation pair were read from the running system, not from documentation.
3. **Source cross-checks** — each architectural claim (evidence gate, `noqa`-guarded refuse paths,
   empty artifact directory, 486,553 normalised rows with **0 importable**) was confirmed in source
   and reports (`docs/evidence/tests_report.md`, `docs/STAGE8_TECHNICAL_AUDIT.md`).

## IV. Results

### IV.A Verified software functionality (evidence class E1)

- Health accurate and truthful incl. the index-provisioning fix (Stage 8); a regression test
  (`test_database.py`, 14 pass) asserts an index failure is never reported as "unreachable".
- Storage (live): pans **2**, observations **3**, weather **3**, outcomes **2** — every row carries a
  demonstration marker.
- ML state (live): `trained: false`, `evidential_models: 0`; empty `ml/models/`; `GET /api/models/`
  empty; no experiment.
- Honest refusals (live): invalid feedback action → **422**; sensor ingestion → **501**; weather
  providers → `registered: []`; auth and export → `not_implemented`; ML-facing endpoints refuse
  where no genuine model exists.
- Full API surface: **91 endpoints, 21 prefixes** (verified via `/openapi.json`).

### IV.B Physics-based simulation, hand-checkable (evidence class E2)

For the demonstration pan (`A = 12 000 m²`, `D0 = 0.182 m`, `C0 = 278.5 g/L`, `R = 35 mm`):
`V0 = 2184 m³`, `Vr = 420 m³`, `V1 = 2604 m³`, `C1 = 278.5 × 2184/2604 = 233.5806 g/L`,
dilution 16.12 %. The API returned `233.5806…`, exactly. This is arithmetic, verified by hand, and
explicitly **not** a predictive claim.

### IV.C Demonstration results (evidence class E3 — labelled worked example)

The full operator loop ran end-to-end on pan `kzn-2026-demo9`, whose location field literally reads
"Demonstration site (worked example, not a field site)": register (201) → observation
(`data_source: demonstration`) → forecast (`source: demonstration`) → twin refresh (complete,
provenance `observed`) → mass balance → **TRANSFER BRINE** advice (`ml_result: null`, rule v2 hash)
→ operator feedback (422 then 201 — the closed vocabulary enforced) → outcome
(`is_demonstration: true`) → comparison and validation (200). Identifiers and request bodies:
`docs/STAGE8_DEMO_GUIDE.md`.

**Prediction comparisons (n = 1 per comparison, demonstration-only):**

| Comparison | Predicted | Actual | Signed error |
|-----------|-----------|--------|--------------|
| Forecast vs actual rainfall (mm) | 35.00 | 32.0 | +3.0 |
| Simulated vs actual salinity, from rainfall that fell (g/L) | 236.855 | 271.2 | −34.345 |
| Simulated vs actual salinity, from forecast rainfall (g/L) | 233.581 | 271.2 | −37.619 |

MAE = bias = **34.345 g/L** at n = 1; coverage **50 %** because the older smoke-check event has no
post-event salinity and is reported `not_comparable` rather than counted as zero. These numbers
demonstrate that the comparison machinery works and is honest; they are **not** simulator-skill
evidence (n = 1, synthetic inputs).

### IV.D Genuine field measurements (evidence class E4 — ABSENT BY DESIGN)

No field campaign has run. Zero field-collected observations exist in the system; none were
invented. Public-hydrology normalisation produced 486,553 rows, which were **0 importable** as
training data because they do not carry salt-pan brine salinity/depth in the required units
(`docs/REAL_DATA_ACQUISITION_REPORT.md`). The field sheet and campaign-provenance stub exist to
collect genuine data (`docs/FIELD_DATA_COLLECTION_SHEET.md`, `data/templates/README.md`).

### IV.E Trained ML results (evidence class E5 — ABSENT BY DESIGN)

No model is trained, evaluated, explained, deployed or retired. Every such endpoint refuses.
`GET /api/evaluation/…`, risk prediction, SHAP attribution and deployment refuse when no genuine
model exists; the system's own status reports the absence. This is state, not capability, and is
stated as such.

## V. Evidence classification (explicit, per requirement)

| Class | What it is | Status here | How you can tell |
|-------|-----------|-------------|------------------|
| **E1** Verified software functionality | Modules behave to spec; gates refuse; failures are reported | **Present — fully verified** | 1086 + 816 tests, lint, live statuses |
| **E2** Physics-based simulation | Transparent arithmetic, hand-checkable | **Present — checkable** | conservation to 1e-9; worked example in §IV.B |
| **E3** Demonstration results | Labelled worked examples, provenance-forced | **Present — n = 1, labelled** | `data_source`/`source`/`is_demonstration` markers in every record |
| **E4** Genuine field measurements | Real pan measurements with campaign provenance | **Absent** | no `manual`-track rows; evidence gate returns no GENUINE dataset |
| **E5** Trained ML results | Held-out scores, SHAP, deployment of a real model | **Absent** | `trained: false`, `evidential_models: 0`, all refusals |

## VI. Discussion and limitations

The design position is "evidence before metrics": the system can never report a metric it is not
entitled to, and the cost is that no field statistics can yet be presented. The benefit is that when
genuine campaign data exists the pipeline lights up under the gates it was built with, without
redesign. Verified limitations (full register `docs/evidence/limitations.md`): no
authentication/authorisation (OPEN ACCESS API; `actor` recorded but never verified), no sensor
gateway, no weather-API wiring, first-order simulator, single-machine deployment, and no git history
at audit time. A health-reporting defect (index failure mislabelled "unreachable") found during the
audit was fixed with a regression test.

## VII. Reproducibility and verification artifact

Snapshot commands reproduce every figure: health, module statuses, storage provenance, the 91-path
surface, the comparison pairs and the full test suites (`docs/evidence/reproducibility.md`). The
claim-verification ledger (Appendix A) maps each statement in this manuscript to the mechanism that
verified it. The field sheet and research-requirements register (`docs/RESEARCH_REQUIREMENTS.md`)
define the exact data and gates that would add E4/E5 evidence.

## VIII. Conclusion

SaltLens demonstrates a verified, evidence-gated DSS whose entire operator loop runs live on
labelled demonstration data while its ML layer waits, gated, for genuinely provenanced field data.
The paper claims operational verifiability and reproducible honesty—not scientific field validation,
which is the content of the research-requirements register that accompanies it.

## References

None are cited. Per the standing constraint, no DOI or bibliographic entry is fabricated; verified
references will be added only from the eventual literature review.

## Appendix A — Claim verification ledger

| # | Claim in this paper | Verified by | Evidence file / command |
|---|--------------------|-------------|--------------------------|
| A1 | 91 endpoints, 21 prefixes | `/openapi.json` live | `docs/evidence/reproducibility.md` §5 |
| A2 | health connected + indexes_ready | `GET /api/health` live | `docs/evidence/reproducibility.md` §2 |
| A3 | 1086 / 816 tests; tsc/eslint/ruff clean | suites re-run 2026-09-30 | `docs/evidence/tests_report.md`, §III.1 |
| A4 | storage 2/3/3/2 all labelled | `GET /api/{pans,observations,weather,outcomes}/` | `docs/evidence/dataset_provenance.md` |
| A5 | `trained:false, evidential_models:0`, `ml/models` empty | `GET /api/ml/`, directory listing | `docs/evidence/methodology.md` |
| A6 | rule v2 sha256 `06a32ee5…4d7b` travel with advice | `GET /api/decisions/kzn-2026-demo9/latest` | `docs/evidence/explainability.md` |
| A7 | mass-balance 233.5806 g/L | hand computation = API value | `docs/evidence/equations_and_assumptions.md` §1 |
| A8 | pairs +3.0 / −34.345 / −37.619 at n = 1; MAE=bias 34.345; coverage 50 % | `GET /api/validation/pairs`, `/outcomes/comparison` | `docs/evidence/prediction_evidence.md` |
| A9 | deployment gate refuses non-genuine | `test_deployment_api.py`, source | `docs/ML_DEPLOYMENT_REPORT.md` |
| A10 | 486,553 rows / 0 importable | `docs/REAL_DATA_ACQUISITION_REPORT.md` | `docs/evidence/dataset_provenance.md` |

## Appendix B — Formatting notes

Ready to ingest into the IEEE (IEEEtran) two-column template: supply the author block, choose the
reference style when verified citations exist, and render Appendix A as a table in the required
margins. Do not add references or accuracy numbers beyond Appendix A and the research-requirements
register unless the evidence gates in `docs/RESEARCH_REQUIREMENTS.md` have fired.