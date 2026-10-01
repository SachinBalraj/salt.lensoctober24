# Stage 7 — ML Deployment, Explainability and System Integration

**Deployment status: `ML NOT DEPLOYED`. Nothing was deployed, nothing predicts,
nothing was explained of a real model, and every surface that could have
fabricated a number instead refused with a named reason.** Stage 6 found the
training layer has no field data (`ML NOT READY`); Stage 7 confirms the
deployment layer has nothing it will put into service, and closes the one gap
that sat between a genuinely trained model and a deployed one.

The gate Contract is unchanged and was re-verified live for every claim below:
*a prediction is answered only by a model version explicitly deployed by a
person, and by no other. There is no fallback model, no default version, and no
heuristic path reachable when nothing is deployed.*

---

## 1. Deployment status

`ML NOT DEPLOYED`. Measured live:

```
/api/models/  total=0  deployed=0
/api/ml/      trained=false  evidential_models=0
/api/experiments/  total=0
ml/models/ artifacts  … 0
/data/processed/   datasets  … 0
```

Task 1's clause "Deploy only a genuinely trained, versioned model with matching
preprocessing, documented features, target and held-out evaluation. **Otherwise
keep ML unavailable**" is satisfied by keeping it unavailable: there are no
model versions, so there is nothing whose evidence, preprocessing or evaluation
could be checked — and the Deployment service refuses to *create* a state it
cannot vouch for.

### 1.1 The one gate that was missing: deployment re-reads evidence

The evaluation layer (Module 09) refuses to write a report for a model whose
training rows are not genuine, so a report's existence is normally proof the
model was evidential *on the day the report was written*. Deployment checked the
report's existence and not the model's stored evidence — the same trusting-a-
stored-fact defect Stage 6 found in the training path (leakage was checked at
build but not at train, and is now checked there too).

Closed in `app/services/deployment.py` with the same remedy: the store is
re-read at the moment it matters. Validation and deployment now call
`_require_evidential(model)`, which rebuilds the verdict from the version's
recorded `parameters["evidence"]` (`verdict_from_parameters`) and refuses with a
new code `non_evidential_model` when the verdict is not `genuine`.

- A version whose evidence was **removed or left blank** after evaluation (the
  exact shape a restore or an edit produces) is refused, even with a genuine
  report present.
- The refusal message names the problem ("a deployment is a claim the model
  answers an operational question with the performance its report measures"),
  so a reader is not sent hunting through the evaluation service to learn why a
  model with a report is not deployable.
- **Ordering kept deliberate.** Evidence is checked first because it is the
  more fundamental claim; a trained-but-unreported model still falls through to
  `not_evaluated`, so the existing gate's answers are unchanged.

This is belt-and-suspenders — the evaluation gate already makes the situation
rare — and that is the point. Single gates trusting single stored facts are the
failure this project has now found three times (leakage at build, evidence at
evaluation). Each is closed by re-reading the record at the next lifecycle edge:
build → train → evaluate → **validate/deploy**.

Two tests added (`tests/test_deployment_api.py`): one wipes the recorded
evidence of a trained model and expects validation to refuse with
`non_evidential_model`; one wipes it from an *evaluated* model and expects
deployment to refuse — proving the report alone is not the gate. Both fixtures
train through the real pipeline, so the happy path is unchanged and the 33
existing lifecycle tests all still pass.

---

## 2. Versions

| Registry | Stored | In service |
|---|---|---|
| Model versions | 0 | 0 deployed |
| Experiments | 0 | — |
| Evaluations | 0 reports | 0 accepted |
| Decision rules | operator rule, v2 | nameless sha256 rule, deployed |
| Datasets | 2 (both demo fixtures) | — |
| Digital twin | v0.1.0 | per-pan snapshots |

The only versioned artifact actually in service is the operator's decision rule
(`rule`→v2, content hash
`sha256:06a32ee5d7f61912af5327b3ae7725c0576cc8e1640601f7de8bb96fbd9a4d7b`),
which is the rules engine's business and not a model.

---

## 3. Prediction API

`POST /api/risk/predict` with no model deployed:

```
409 Conflict
detail:  no_deployed_model  No model version is deployed for target
         operational_risk, so this module will not predict. Operational
         prediction is answered only by a model version whose status is
         deployed, and no other. Train, evaluate and explicitly deploy a
         version through Module 17 first (POST /api/models/{id}/validate then
         /deploy).
```

Reads that land before the gate are also validated: an unknown pan and a twin
with no observed features each produce their own refusal (`no_twin_state`,
`no_observed_features`), not a substituted estimate. `{}` body → 422. The
prediction service (`app/services/prediction.py`) has **eight** named refusal
codes — `no_deployed_model`, `model_not_fitted_to_risk_rule`,
`risk_rule_unavailable`, `no_twin_state`, `no_observed_features`,
`feature_mismatch`, `artifact_unavailable`, `feature_not_knowable_at_prediction
_time` — covering Task 2's required validations (pan ID, feature set, units,
timestamps, schema, model versions) and Task 7's failure modes (missing
artifact, stale/missing inputs, schema mismatch, version mismatch). None of the
eight returns a prediction.

Task 2's "return errors rather than fabricated predictions" is satisfied
structurally: the estimator is unreachable without a deployed version (`Module 17`),
features come only from the digital twin (never from the request body), and the
row handed to the estimator holds exactly the deployed model's declared
`feature_names`, imputing only what the fitted preprocessor would have.

---

## 4. SHAP

Task 3: "Implement SHAP only for a real compatible trained model. ... do not
claim causation."

- **`shap 0.52.0` is genuinely installed and used** (`app/services/explain.py`
  imports it). Tree-based estimators get `TreeExplainer`; the permutation
  fallback exists for the other candidates — but no estimator is reachable via
  the deployment gate, so **no SHAP value exists and none is produced**.
- The route `/api/explain/predictions/{id}` explains **only a stored prediction
  that names its model** — SHAP of an absent prediction is refused, not hoped
  for. A class-with-no-probability prediction is refused rather than attributed
  on a decision margin the operator never saw.
- `/api/explain/models/{id}/versions/{v}/importance` for an unknown version →
  `404 no_such_model_version` (reproduced live).
- The card (`src/components/dashboard/ExplainabilityCard.tsx`) never claims
  causation. Contributions are **signed probability-point changes around zero,
  never percentages** ("+0.25 does not make the pan 25% more likely"); factors
  resting on an imputed value or on a simulated figure are **marked per row**;
  the model's probability is deliberately not placed beside them.
- Explanation availability is reported (`available` / `unavailable` /
  `shap_computation_failed`), so a stalled SHAP computation is never silently a
  zero contribution.

---

## 5. Digital twin ↔ physics ↔ ML keeping outputs distinct

`/api/twin/{pan_id}/state` (verified live) returns a **fairly assembled join** of
the newest observation, the newest observed/next weather, and the pan
registration, with `observation.source="demonstration"` attached to the record,
`is_complete=false`, a `missing_inputs` list naming exactly which quantity is
absent and why, and every column carried or absent — no derived or carried-
forward values. The docstring's contract ("a stale twin is visible rather than
silent") is honoured: the observation's timestamp rides with the reading.

The mass-balance simulation runs over the twin's recorded values only. An ML
risk estimate would come only from the deployed model; with none deployed, the
two engines are kept distinct by never being mixed:

- Twin → `observed`/forecast, simulation → `simulated`, model → `ml` (each its
  own provenance kind on the dashboard legend).
- A rule that consults the model refuses to attach the model's answer when no
  prediction exists.

---

## 6. Recommendation engine and physics-only operation

`POST /api/decisions/advise {pan_id}` (verified live):

```
recommendation  HARVEST NOW
basis           physics_and_rules
rule_version    Operator-configured harvest and flood rule v2 (sha256:…)
ml_result       null
notice          "…ML prediction unavailable. The rule applied here declares no
                prediction variable, so the stored model estimate was not read.
                A rule that does not consult the model must not have the model's
                answer attached to it as though it had. DEMONSTRATION ONLY: built
                from demonstration data (observation_data_source,
                weather_forecast_source). This is a worked example, …"
simulation_result  {model_name: rainfall-dilution-mass-balance-v1, is_ml: false,
                    post_rain_salinity_g_l, run_id, rainfall_source: …}
reason           "Rule '…' v2: simulated_post_rain_salinity_g_l lte
                  target_harvest_salinity_g_l (read 246.842… from simulation) — the match decided."
```

Task 5's conditions hold: **physics-only operation is preserved when valid**
(`basis: physics_and_rules`), the decision cites the exact rule version and the
readings the match decided on, the stored model estimate is **null** when no
prediction exists (the engine's own rule-protein says a decision that does not
consult the model is not allowed to display its number), and the output
**labelled demo provenance**. An unknown pan → `409 no_twin_state`. No threshold,
no body field, is ever defaulted in.

---

## 7. Dashboard

The screen was already built around Task 6's list, and it exercises every item:

| Task item | Where |
|---|---|
| Observation freshness | every reading's tile footnote shows its source and timestamp (`ReadingTile.fromTimestamp`) |
| Forecast | WeatherCard (observed + forecast windows) |
| Physics estimate | SimulationCard (mass-balance sweep, forecast marker) |
| ML risk **if available** | MlRiskCard — gates the button on the deployed-model status read on mount; absent → the card states no model is in service, no figure |
| Explanation **if available** | ExplainabilityCard — loadable only when a prediction exists |
| Recommendation/rule version | RecommendationCard shows `rule_version` + `basis` |
| Operator feedback | Decision panel + TraceCard (feedback record, demo sources) |
| **Label demonstration data** | `DemonstrationSimulationCard`, demo-toned notices on the data-entry and decision panels, `DEMONSTRATION NOTICE` copy, and the six-kind provenance legend that never mixes recorded/forecast/simulated/ml/rule/decision |

The dashboard footer states the working rule: "Nothing on this screen is a
simulated measurement, and no figure is filled in where a record is missing."
No frontend change was required; the stage's contribution to this task was
verifying the claims above against the running system and recording them.

---

## 8. Tests

```
backend  1085 passed  (1083 before Stage 7 + 2 new), 0 failed, exit 0
   new:  test_validation_refuses_a_model_whose_recorded_evidence_is_not_genuine
         test_deployment_refuses_a_model_whose_recorded_evidence_is_not_genuine
         (both wipe the stored evidence of a real pipeline-trained model and
         expect code non_evidential_model; the evaluated-variant proves the
         report alone is not the gate)
frontend  39 files, 816 passed; tsc --noEmit exit 0; eslint exit 0  (unchanged)
ruff     clean on every file touched (app/services/deployment.py,
         tests/test_deployment_api.py)
```

Failure modes Task 7 lists, and where each is covered:

| Mode | Covered by |
|---|---|
| Missing artifacts | prediction `artifact_unavailable`; registry `no_such_model_version`; explain `no_such_model_version` (live-verified) |
| Stale inputs | twin reports observation age + missing-input accounting; prediction refuses a twin with no observed features; no freshness *threshold* exists — see §10 |
| Schema mismatches | feature_mismatch refusal path; dataset id+version matching at rule time; Pydantic 422s enforced by `extra=forbid` on lifecycle bodies |
| API failures | refusal codes are stable strings (`refusal` field), uniformly handled; router↔service tests |
| Unavailable SHAP | explain refusals (`no_numeric_output`, `no_such_prediction`, `shap_computation_failed`) tested in `test_explain.py` |
| DB failures | `DatabaseUnavailableError` tested in `tests/test_database.py` |
| Unauthorized access | **not testable — no authorization boundary exists; see §10** |
| Version mismatches | deploy requires an exact version; dataset matched on id *and* version; incumbent-supersede policy tested in 6 lifecycle tests |
| Never hardcode fallback risk | `no_deployed_model` surfaces refuse; `ml_result` null + notice; decisions basis `physics_and_rules`; probe: a `rules` path that refuses to show a model number it does not read |

---

## 9. Unavailable features

- **ML deployment and operational prediction.** No version meets the gate, so
  `no_deployed_model` is the only answer `/api/risk/predict` can give. Correct
  per Task 1's "otherwise keep ML unavailable".
- **SHAP for a real model.** None can exist until a compatible model is
  deployed. The code path exists, is tested on the maths level, and is sealed
  off from the live database by the same gate.
- **Models / experiments / evaluations.** Ledgers are empty and remain so.
- **Model-origin recommendation influence.** Rules consult the model only when
  a deployed prediction exists; with none, `ml_result` is null by design.

---

## 10. Limitations

- **No authorization boundary exists.** The API records `actor` and refuses to
  fabricate, but nothing authenticates a caller or authorises a role — any
  process that can reach `:8000` can call `/api/models/{id}/validate` or `deploy`
  with a self-attested actor. This is a research/prototype deployment and it is
  **not** mistaken for an operational control: Task 7's "unauthorized access"
  is reported here as an absent feature rather than a tested one, and the report
  will not claim a security boundary that does not exist. Adding one (API keys
  at minimum) is a genuine next action, not a cosmetic one.
- **No freshness threshold.** The twin makes age visible and refuses a twin with
  no observed features, but no ceiling decides "this reading is too old to
  predict from". A threshold would be an operational policy and was deliberately
  not hardcoded into the pipeline (the same discipline as the never-default rule);
  it should be a configured tolerance, not a constant.
- **No model means no migration of the "SHAP only when compatible" rule.** A
  non-tree candidate with the permutation fallback, and a tree candidate with
  `TreeExplainer`, are coded but have no live input to run against — the
  compatibility switch is unexercised on real artifacts.
- **The two Stage 5/6 data blockers stand.** No labels (`harvest_outcome` is
  null on the only outcome) and no compatible observations (486,553 public rows,
  all outside the pan-scoped schema). Deployment readiness depends on the field
  campaign, exactly as Stage 6 reported.
- **`evidential_models: 0` is the truth but not a count.** There are zero model
  versions, so a live registry-derived count would also print 0; it is stated as
  a status constant until a model can be registered.

---

## 11. Next action

1. **Authentication/authorization** on the deployment and feedback lifecycle
   (`actor` is recorded, not verified) — the only real gap found that is not
   data-gated.
2. **Operator-configurable freshness tolerance**, so "stale" becomes a decision
   the operator set rather than a constant the project chose.
3. **Field campaign** (Stage 6 §13) — nothing in this stage shortens it; the
   deployment gate is now verified to do the one thing it must: refuse.
4. When a genuine, evaluated model exists: deploy a version, then re-verify
   prediction, twin-sourced features, SHAP compatibility, and the dashboard's ML
   card end-to-end — the tests are in place for all of it.

---

## File manifest

```
backend/app/services/deployment.py    deploy/validate re-read the version's
                                      recorded evidence (non_evidential_model)
backend/tests/test_deployment_api.py  +2 tests; gate is report+evidence, not
                                      report alone
docs/ML_TRAINING_REPORT.md            Stage 6 (evidence gate, readiness)
docs/REAL_DATA_ACQUISITION_REPORT.md  Stage 5 (486,553 rows; both blockers)
```