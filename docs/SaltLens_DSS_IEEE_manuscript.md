# SaltLens DSS: An AI-Driven Digital Twin Decision Support System for Climate-Resilient Salt-Pan Management

**Draft manuscript (working title). Status: internal draft for review — no submission-ready claims.**

_Draft date: 2026-09-30. All measurements and system-state claims trace to `docs/evidence/`
(`reproducibility.md`). This draft states capacity and architecture truthfully; it does **not**
claim field validation that does not exist._

---

## Abstract (draft)

Climate variability threatens artisanal salt-pan brine operations that have no access to
instrumentation, forecasting or decision analytics. This paper presents **SaltLens DSS**, a
three-tier decision support system that is designed to become AI-driven: a FastAPI/Next.js
application; a per-pan digital twin that joins operator observations with weather forecasts; and a
first-order rainfall dilution simulator whose every figure is checkable by hand. The system is
built so that machine-learned classifiers, evaluations, explanations and deployments are **evidence
gated**: any dataset, model or table that is not provably genuine field data is refused with a
machine-readable code instead of being presented as research evidence. On the reference deployment,
the whole operator loop — pan registration, observation, forecast, twin refresh, simulation,
advice, operator feedback, outcome recording and validation — runs end-to-end on explicitly
labelled demonstration data, while ML-facing endpoints remain deliberately empty because no genuine
field dataset exists. We argue the architecture is research-ready (reproducible, auditable,
falsifiable) and production-shaped, and we specify, without yet delivering, the field-campaign and
ML-enablement path. **No field validation is claimed in this draft.**

## 1. Introduction

Salt pans are shallow engineered basins where brine is concentrated through evaporation; a large
rainfall event dilutes the brine and can force emergency harvest or brine transfer. Producers
without sensors and forecast tools currently "wait and taste"; a digitised twin of brine depth and
salinity against a rainfall forecast could convert that into an auditable recommendation.

### Contribution

1. An **evidence-gated ML architecture** in which training/evaluation/explain/deploy refuse
   non-genuine data, so the system can never report metrics it is not entitled to.
2. A **transparent first-order simulator** whose assumptions and intermediate quantities travel
   with every result.
3. A **demonstration-validated operator loop** (all steps live) with explicit labelling so demo
   numbers can never be mistaken for measurements.
4. Explicit **field-readiness path** (template + gate) for the genuine data ML will need.

### Position in the literature (stated without fabricating citations)

Mass-balance / dilution modelling of shallow evaporitic basins is well documented; weather-driven
salinity forecasting typically sits on either physical models or data-driven regressions. The
novel claim here is not a new equation but a workflow discipline: the DSS **refuses** to let a
demo-trained model or a public-hydrology row masquerade as field evidence. Evaluation against
genuine deployments is future work.

## 2. System Design

### 2.1 Topology

- **Data plane:** MongoDB Atlas (`saltlens`); surfaces via a MongoClient whose health endpoint
  reports `connected`, `indexes_ready` and an accurate `error`.
- **Service plane:** 19 FastAPI modules (pans, observations, weather, twin, simulator, datasets,
  ml-training, evaluation, risk, explain, decisions, dashboard, feedback, outcomes, validation,
  model registry, export, experiments). Module status is self-reported; not-implemented modules say
  so.
- **Presentation:** Next.js operator UI, client-rendered dashboard over the same APIs.

### 2.2 Chains of trust: the evidence gate

`provenance.py` classifies any dataset as GENUINE/DEMONSTRATION/MIXED/UNKNOWN and fails closed.
Training (`ml.py`), evaluation (`evaluation.py`) and deployment (`deployment.py`) each re-read the
provenance at decision time. Deployment is the strongest actor: at `validate`/`deploy` it re-reads
the model version's recorded evidence and refuses non-genuine versions (`NON_EVIDENTIAL_MODEL`).

### 2.3 The twin and the simulator

The digital twin (M2.5) is a join of the pan, its observations and its weather — "a join, not a
model". The simulator (2.6) is the volume/salt conservation of a rainfall event (Equation 1 in
`docs/evidence/equations_and_assumptions.md`), returning intermediate volumes and mass and a
conservation check.

## 3. The Decision Pipe (current: rules + physics)

Four-action closed vocabulary: HARVEST NOW / TRANSFER BRINE / PROTECT / DRAIN / WAIT AND RECHECK.
A versioned rule (sha256 `06a32ee5…`) selects the action from twin state; the rule hash, the notice
"ML prediction unavailable", and `basis: physics_and_rules` are returned with every recommendation
so an operator sees exactly what drove the advice. ML, when enabled, will supply `ml_result` as a
component input and the recommendation's basis will report provenance.

## 4. Demonstration (explicitly labelled "worked example, not a field site")

Reference deployment submitted a synthetic pan (`kzn-2026-demo9`), observation (278.5 g/L, 18.2 cm),
forecast (35 mm), refreshed the twin, ran the mass balance (post-rain 233.58 g/L), received a
TRANSFER BRINE advice with `ml_result: null`, recorded operator feedback (with a validating 422 for
an out-of-vocabulary action), recorded an outcome (post 271.2 g/L), and produced comparison metrics
(§5). Every item is `data_source/source/is_demonstration`-labelled.

## 5. Results (as they exist today)

- **Mechanical:** 1086 backend tests, 816 frontend tests, tsc/eslint clean, ruff clean
  (`docs/evidence/tests_report.md`). Health reporting is now truthful for index failures.
- **Prediction comparisons (demonstration, n = 1):** forecast-vs-actual rainfall error +3 mm;
  simulated-vs-actual salinity error −34.345 g/L (from the rainfall that fell) and −37.62 g/L (from
  forecast rainfall); MAE = bias = 34.345 g/L at n = 1, coverage 50% (the older demo event has no
  post-event salinity). Reported **without** any claim of simulator skill.
- **ML:** `evidential_models = 0`; no model, no experiment, no deployment; explain refuses. This is
  reported as state, not as capability.

## 6. Discussion

The architecture is engineered around one position: **evidence before metrics**. The cost is that
this paper cannot yet report field validation — no such data was collected. The benefit is that the
moment genuine campaign data exists (per `FIELD_COLLECTION_TEMPLATE.md`), the entire pipeline
(training → evaluation → explanation → deployment → validation) lights up under the same gates it
was built with. We treat "the system refuses" as a measured, documented behaviour rather than a gap.

Known limitations (full list in `docs/evidence/limitations.md`): no auth yet (OPEN ACCESS), no sensor
gateway, no weather-API wiring, first-order simulator, single-machine deployment.

## 7. Reproducibility

Snapshot 2026-09-30; commands in `docs/evidence/reproducibility.md` reproduce health, tests, module
status, storage provenance, and every comparison pair above. No git commit existed at audit time;
all counts are read from the live system.

## 8. Conclusion (draft)

SaltLens demonstrates a research-ready, evidence-gated DSS whose operator loop is fully live on
labelled demonstration data while its ML layer waits, gated, for genuinely provenanced field data.
Its architecture is built to be honest under scarcity and to light up without redesign once the
data arrives.

## References

_None cited in this draft. Per constraint, no DOI or bibliographic entry is fabricated. When a
submission draft is prepared, only verified sources will be added._

## Appendices

- A. Equation set and assumptions (`docs/evidence/equations_and_assumptions.md`)
- B. Provenance model (`docs/evidence/methodology.md`, `docs/evidence/architecture.md`)
- C. Test report and reproducibility (`docs/evidence/tests_report.md`, `docs/evidence/reproducibility.md`)
- D. Limitation register (`docs/evidence/limitations.md`)