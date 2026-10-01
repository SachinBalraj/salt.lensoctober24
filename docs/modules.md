# Module map

The system is decomposed into 18 modules. Each maps to a FastAPI router that
answers at its path; a built module reports `status: "implemented"` with live
counts, and an unbuilt one reports `not_implemented` (no fake data). Modules
are added one at a time; the module number appears in the router `tags` and
OpenAPI grouping.

| #  | Module                          | Router prefix         |
| -- | ------------------------------- | --------------------- |
| 1  | Authentication                  | `/api/auth`           |
| 2  | Salt-pan registration           | `/api/pans`           |
| 3  | Pan observation / data entry    | `/api/observations`   |
| 4  | Weather observation & forecast  | `/api/weather`        |
| 5  | Pan-specific digital twin       | `/api/twin`           |
| 6  | Rainfall mass-balance simulator | `/api/simulator`      |
| 7  | Dataset management              | `/api/datasets`       |
| 8  | ML training pipeline            | `/api/ml`             |
| 9  | Model evaluation / comparison   | `/api/evaluation`     |
| 10 | Risk definitions & class distribution | `/api/risk`        |
| 11 | SHAP explainability             | `/api/explain`        |
| 12 | Decision-support engine         | `/api/decisions`      |
| 13 | Operator dashboard              | `/api/dashboard`      |
| 14 | Operator feedback               | `/api/feedback`       |
| 15 | Actual-outcome recording        | `/api/outcomes`       |
| 16 | Predicted-vs-actual validation  | `/api/validation`     |
| 17 | Model versioning / retraining   | `/api/model-registry` |
| 18 | Research data export            | `/api/export`         |

Module 02 is the first built module. It registers, lists, opens, edits and
deactivates salt pans, and the frontend pages under `/pans` drive those six
endpoints.
Module 03 records and reads pan observations. The form on the pan detail page
writes readings through `POST /api/observations/`; the pan's current observed
state is maintained server-side and the history and trend charts read from
`GET /api/observations/`. Manual entry is complete. Sensor ingestion is
present as architecture only: `POST /api/observations/ingest/sensor` reports
501 and `data_source: "sensor"` is refused with 409 until a real gateway is
connected, so no reading can ever claim a provenance no instrument produced.

Module 04 records weather for a pan. Manual entry through
`POST /api/weather/` is complete, and every stored record keeps the source it
was given, rendered on each row in the UI. The forecast path is built but has
nothing behind it: implement `WeatherProvider`, register it, and
`POST /api/weather/ingest/{provider_name}` works against a real service with no
change to the schemas, routes, storage or UI. Until then the registry is empty
and the route answers 404 rather than inventing a forecast.

Provenance here is structural, not advisory. `source: "weather_api"` is
rejected on manual entry with 422, and the ingest route sets that value itself
from the registered provider, so no request body can file a hand-typed figure
as machine-retrieved. `source: "sensor"` is refused with 409 while no weather
gateway is connected, for the same reason Module 03 refuses it.

Module 05 assembles each pan's digital twin from those records. It is a join, not
a calculation: `GET /api/twin/{pan_id}/state` selects the newest pan observation,
the newest non-forecast weather record, the newest weather forecast and the pan's
static registration, and copies their values across unchanged.

Selection is "latest by the instant the record describes", not latest by
insertion order, so a backfilled reading cannot become the current state just
because it was typed today. Observation and forecast are kept apart by the query
itself: an observed rainfall figure never fills `forecast_rainfall_mm`, and a
forecast never supplies an observed quantity.

The twin does not invent. A quantity no record supplied stays `null`, is named in
`missing_fields`, and carries a sentence in `missing_inputs` saying which record
would fill it. Nothing is carried forward from an older reading and nothing is
averaged, because a stale figure wearing a current timestamp is a fabrication. A
measured `0` is kept distinct from an unmeasured quantity throughout.

Assembly time and evidence age are reported separately: `timestamp` is when the
state was assembled, and each input carries its own `timestamp` and
`age_seconds`, so a twin built this second out of last week's readings is visibly
not current.

`GET /api/twin/{pan_id}/state` stores nothing — a page view cannot manufacture
history. `POST /api/twin/{pan_id}/refresh` writes an immutable snapshot, and its
`changed` flag distinguishes a real change from a no-op, so a change log built on
the history stays meaningful. `GET /api/twin/{pan_id}/history` returns those
snapshots, each keeping the record it was built from.

The four derived fields on the model — `brine_volume_m3`, `brine_mass_kg`,
`estimated_crust_cm`, `confidence` — stay `null` in the twin. Module 06 computes
its own brine volume and salt mass for the inputs it is given, but does not write
them back onto a twin, because a what-if answer is a scenario and not a
measurement of the pan.

See [api.md](api.md) for the contract.

Module 06 is the what-if rainfall simulator, and it is arithmetic rather than
learning. Given a pan area, a starting brine depth, a starting salinity and a
rainfall depth, it applies conservation of volume and conservation of salt:

```
V0 = A × D0              initial brine volume      (m³)
Vr = A × R               rainfall volume           (m³)
V1 = V0 + Vr             post-rain volume          (m³)
C1 = C0 × V0 / V1        post-rain salinity        (g/L)
Dilution % = ((C0 − C1) / C0) × 100
```

`POST /api/simulator/rainfall-scenarios` sweeps the six preset depths — 0, 5,
10, 20, 50 and 100 mm — plus any custom depths, and
`POST /api/simulator/mass-balance` resolves a single depth. The panel on the pan
detail page drives both and charts rainfall against predicted salinity.

Two properties are deliberate. The salt the pan holds is reported before and
after the event and is the same number, which is what makes the drop in
salinity arithmetic rather than salt loss. And a starting salinity of 0 g/L
reports `dilution_percent: null` with a reason, because the percentage is a
ratio against the starting salinity and there is none; it is never rendered as
a zero.

Every response carries `is_simulated: true`, the equations, the disclaimer
("Physics-based simulation under simplifying assumptions; not an ML
prediction.") and the full assumption list: complete mixing, no evaporation, no
drainage, no overflow, no seepage, no brine transfer, no crystallization during
the calculation, and rainfall as one instantaneous depth. The module needs no
database — the balance is fully determined by its four inputs.

Module 07 assembles the research datasets that Modules 08–11 will train and
evaluate on, and it is the module that decides what a row is allowed to contain.
Rows are built only from stored pan observations, weather records and recorded
outcomes; there is no endpoint that accepts a number, so a value no instrument
produced cannot enter a training file.

The grain is one row per pan observation, anchored at that observation's instant.
Every feature is read from a record that existed at or before the anchor, and the
target is read from a measurement taken at or after the anchor plus a horizon the
caller configures. `GET /api/datasets/catalog` serves the whole feature set, the
target set, the causal policy and the documented exclusions without touching the
database, so a researcher can read what would be built before there is any data to
build it from.

There is no default target. A build names one of the five server-side targets and
supplies a horizon; the server then owns the label column, whether the target is a
regression or a classification, and whether it needs bin edges. The banded target
refuses to build without edges, because choosing where the class boundaries fall
would make the class distribution a fact about that choice rather than about the
pans. A `horizon_hours` of zero is refused, because a label measured at the anchor
describes the state rather than its consequence.

`POST /api/datasets/` returns the full report with the manifest: record count, date
range, pan count, per-column missingness with reasons, numeric and categorical
distributions, the class distribution, duplicate groups, the data source behind
every row, the leakage audit, and a list of the things a researcher should decide
about. The report is stored with the manifest rather than recomputed on read,
because a report that shifts as readings arrive cannot be cited as the state of the
data at the time of an experiment.

The rows are never stored. The manifest is; the rows are re-derived from current
records on every read, so an export cannot quietly serve a stale snapshot. The
content hash ties the two together and is the dataset's identity, so an experiment
cites a hash rather than a name. Building the same specification over the same
records returns the existing dataset with `created: false` rather than writing a
second document, and `POST /{dataset_id}/rebuild` re-derives the rows and either
confirms the stored hash or stores a successor at the next version with
`supersedes_content_hash` pointing back — the earlier experiment stays citable
against the data as it was.

`GET /{dataset_id}/export` returns the CSV. It carries both hashes and a drift
flag in headers, because the body is the file and a file with a comment prepended
is one `pandas.read_csv` will no longer read as a header row. An empty cell means
the value was never recorded and `0` means it was; nothing writes a zero, a mean or
a carry-forward into a gap.

A build that breaks the causal ordering is refused with a 500 naming the offending
rows rather than stored with a warning attached. A leaked dataset is not a dataset
with a caveat: it produces a score nobody can defend, and a warning on it is a
warning that gets ignored.

See [api.md](api.md) for the contract.

Module 08 trains classification models on those datasets, and it is the module
that decides what a *score* is allowed to mean. It fits four candidates —
logistic regression, a decision tree, gradient boosting and a random forest —
and reports every candidate's held-out metrics, not only the winner's, because a
comparison that shows one number cannot be checked.

Training is synchronous. `POST /api/ml/train` fits and scores in the request and
returns the registered model version. There is no queue and no polling endpoint.
A dataset that cannot support a model is a refusal the caller can act on now;
deferring it behind a status someone has to poll would change nothing except when
the answer arrives.

Four decisions here exist to stop a plausible number from being quoted as though
it meant more than the data supports.

The sufficiency gate refuses before anything is fitted, and says the required
sentence — *"Insufficient labelled data for reliable model training."* — followed
by the specific counts that failed, so the answer says what to collect rather than
only that there is not enough. It has floors for labelled rows, rows per split,
distinct classes, rows per class, training rows per class, distinct pans, and
features. There is no synthetic row, no resampling, no label borrowing and no
weak fallback: a model fitted to 12 rows will produce metrics, and those metrics
will be wrong in a way nobody can detect downstream.

Random Forest is always fitted and always scored, and is never automatically
selected. With a 21-feature tabular dataset it is the candidate that wins most
often while being the hardest to explain and the most inclined to fit pan-specific
quirks, so it is withheld from selection by default and can be permitted
deliberately with `allow_random_forest`, which the run records. Selection is macro
F1, ties broken by balanced accuracy and then by fixed candidate order. Macro F1
rather than accuracy because these targets are imbalanced, and accuracy on a 95/5
split is a statement about the majority class.

The default split is chronological — earliest rows to train, most recent to test —
because that is the only ordering that respects the fact that this system predicts
forward in time. `grouped_by_pan` holds out whole pans and answers the unseen-pan
question, which a random split of a population does not; `per_pan_chronological`
cuts time within each pan and is explicit that it does *not* answer it. Imputation
statistics and the one-hot vocabulary are fitted on training rows only, so no
held-out value informs the model that is scored against it.

Every refusal is a 4xx that names itself, and a refusal writes no artifact and no
registry row. Drift is caught by re-deriving the rows and comparing the current
content hash to the manifest's, with 409 and a rebuild instruction: a version
number citing training data that has since changed is a false provenance claim,
and it is cheaper to catch than to disprove later.

Each registered model version carries the dataset id, version and content hash,
the feature list, the target, the split strategy and its details, the random seed,
the library versions, the training duration, the hyperparameters of every
candidate, and the reason one was selected. The decision tree is a specific,
inspectable structure; a model whose configuration cannot be reproduced cannot be
re-examined when it is wrong.

See [api.md](api.md) for the contract.

Module 09 scores a registered model and shows what the score is allowed to mean.
It reuses Module 08's canonical scorer rather than writing a second one, and it
rescores the *registered artifact* so the number describes the model that is
actually stored rather than a re-fit of it. The other three candidates are
deterministically reconstructed and scored alongside, and the report says so: a
figure for a candidate whose weights were rebuilt is not a figure for the artifact
on disk, and the difference has to be visible.

Every metric is a reading, not a number. A reading carries `value`, `computed`, a
`variant` naming the convention that produced it, and a `reason` when it is
absent, and the difference between those three states is the whole point of the
module. A measured zero is rendered as zero. A `zero_division=0` convention is
rendered as a number with a marker, because the figure is real and its meaning is
narrower than the figure alone suggests. An uncomputed metric is rendered as a
dash with the backend's reason attached — never as `0`, and never as a
zero-height bar in the chart, which a reader scanning six bars takes for a low
score. The tests for this module are mostly about that distinction, and a mutation
that turns an absent reading into a zero is caught by eight of them.

Nothing in the module can be made to grade itself. `POST /api/evaluation/run` takes
a model id and, optionally, a fold count; it forbids unknown keys, so there is no
field in which a client could submit an accuracy alongside a request to be scored.
The cross-validation *design* is not the caller's to choose either — it is derived
from the split the model was fitted with, because a caller who could name the
design could shuffle a time-ordered dataset until the score improved, which is the
one thing a held-out evaluation exists to prevent.

Two refusals are fixed in different ways, so they are told apart. `dataset_drift`
and `artifact_unavailable` are both 409, and the UI says for each what to do; the
first needs a re-train, the second needs the file back. Neither is fixed by
pressing the button again, so neither offers it.

The comparison table states its own criterion, quotes Module 08's rule rather than
restating it, and marks `selected` as a boolean per row. No row is labelled
"best": a rule that decides can be checked against the scores beside it, and a
superlative cannot. The table also reports whether the rows share a denominator,
and refuses to rank rows that do not — the cross-version view returns
`comparison: null` and the reasons rather than a table whose figures come from
different row sets.

One evaluation is stored per `(model_id, version)`, under an id derived from that
pair, with a unique index behind it. Re-running replaces the report and says
`recomputed: true` rather than accumulating rows: two reports for one model
version would leave "how good is this model" with more than one answer.

See [api.md](api.md) for the contract.

Module 10 is about the *label*, not the prediction, and the two are deliberately
separate. A risk model is only as defensible as the severity vocabulary it was
fitted to, and `high`/`medium`/`low` written into a training script is not a
vocabulary anyone can argue with: it cannot be revised without invalidating every
model fitted to it, and it cannot be cited as anything but a constant. So the
vocabulary is data. `POST /api/risk/definitions/` writes a researcher's own
classes, thresholds and reasons, and the module stores them with a version.

`POST /predict` exists and is answered by the deployed model version and by nothing
else. This module trains nothing, so with no deployment it refuses with
`409 no_deployed_model` rather than approximating — the refusal is the designed
behaviour, not a gap, and the status endpoint says so rather than leaving a 404 to
explain it. The features are read from the pan's current digital twin state, in the
order the deployed model declared them, so a caller cannot hand the model a number
of its own choosing.

Two absences remain load-bearing. There is no update or delete: a rule that can be
edited in place leaves every model fitted to it undescribable, so a revision is a
new version and `GET /definitions/name/{name}/versions` shows what changed. And
there are no default classes anywhere — a researcher who posts a rule without a
class named "high" gets a module that has no opinion about severity, and one who
posts nothing gets no labels at all.

A definition names its classes, orders them by rank, and gives each criterion a
variable, an operator, a threshold, a label and a required rationale. The rationale
is not decoration: a threshold with no stated reason is a number a future reader
cannot evaluate. `GET /api/risk/variables` publishes the eleven measurable
quantities a rule may reference, each with the moment it was knowable and how it is
derived, and it is served from the same catalogue the write path validates
against — so what is published is what is enforced. A criterion naming a variable
outside that catalogue, comparing in the wrong unit, or ordering a categorical
value is refused with a 422, because each would produce a class distribution that
is a fact about the mistake rather than about any pan.

The server assigns the identity. The specification hash is computed from the stored
content and never accepted from the request, so a client cannot file a rule under
an identity that does not describe it, and the version is read from the stored
history rather than incremented. Re-posting an unchanged rule returns 200 with the
existing document and `created: false`: posting a rule twice is one rule, and a
second version would record a revision nobody made. The `(name, version)` index is
unique, so two concurrent writers cannot both store "version 2" — the loser
re-reads and takes the next number.

A rule becomes a label through Module 07, not here. It is the `operational_risk`
dataset target: a build names a definition, the builder derives the labels, and
every row records the definition, its version, its specification hash, the label
basis, the source variables it read, the criteria that matched and — where nothing
matched — why. Those columns reach the exported CSV next to the label, because a
file whose label column names a severity but carries no rule cannot be checked
against anything.

The causal audit changes shape for a derived label. "The target was measured at or
after the horizon" is meaningless for a label no measurement produced, so the check
is replaced by a per-variable one: every variable the rule read is compared against
the moment that variable was knowable, because that is what the label is a function
of. A rule reading `harvest_outcome` is filed as retrospective whether or not its
author said so, and a dataset build from one is refused unless the request sets
`allow_retrospective_label: true`. A retrospective label that looks prospective will
be read as a forecast, and the build is the last place that can be caught.

A fallback class is withheld on any row the rule could not fully read. The fallback
means "no criterion fired", which is only a statement the rule can make about a row
it managed to read; assigning it to rows whose data was too thin to evaluate would
put an assumed severity exactly where one is least defensible. Such a row stays
unlabelled and says so.

`POST /definitions/{definition_id}/distribution` reports what a rule's labels look
like across the stored records, before anything is fitted: the class balance, which
declared classes were never observed, how lopsided the split is, and whether the
distribution supports training at all. The rows are derived by the ordinary builder
with an `operational_risk` target pointing at that definition, so these are the
same labels, with the same provenance and the same audit, a dataset would carry —
asking for the distribution is not a shortcut around the audit. Nothing is stored
and nothing is trained; `trained_model` is always null. The same distribution is
stored on the dataset report for a rule-derived build, because the generic target
block cannot name a class the catalogue has never heard of.

The distribution disqualifies itself in four separate ways, because they are four
different problems: nothing was labelled; a declared class received no row, so
nothing can be measured against it; everything landed in one class; or the split is
too lopsided or too thinly populated for a per-class figure to mean anything. An
unobserved class counts on its own — a 50/50 split between two of three declared
classes passes every numeric test, which is exactly the case where a per-class
report would look healthy while being silent on a third of the rule's own
vocabulary.

See [api.md](api.md) for the contract.

Module 11 says which features moved a prediction, and the position of the module is
that it will say so *or* refuse to, with no third option. An explainability feature
that falls back to a correlation, a permutation of its own, or a model's
coefficients produces a ranked list of influences that look exactly like a
SHAP attribution and are not one, and nothing downstream can tell the difference.
So the values come from the `shap` library applied to the registered artifact, or
the response is a named refusal.

Every operational prediction is explained as it is made. The explanation is stored
against the prediction and the model version, and the prediction response carries
its id and its status; a client that wants to know why does not have to remember
to ask. A failure here never costs the caller the model's own answer: the
probability was computed before the explainer was called, so a broken or absent
attribution is reported as `unavailable` with its reason and the class and
probability are returned exactly as they were.

The row explained is the row the model was given, not a row rebuilt from the twin
afterwards. The prediction stored its `input_snapshot` and the
`input_snapshot_id` that hashes it, and a snapshot that no longer matches that hash
is refused with `input_snapshot_mismatch` — explaining a row that looks like it
would attribute a number to input nobody can check.

**Three quantities, reported apart.** The model's probability is the estimator's own
output and is not an explanation of itself. A SHAP contribution is measured in
probability *points* against a stated base value — how far one feature moved the
output from what the model would have said about a typical row — and is not a
probability. A physical simulation result is Module 06's mass balance for the pan,
held in the input row as an *input the model read*, and is neither an output of the
model nor a contribution. They are labelled apart in the stored record and in every
response, because a mass-balance figure, a probability and a change in that
probability are three different claims about a pan.

Two explainers run, and which one is recorded with the values. A tree classifier is
explained by `shap.TreeExplainer` in probability space, which computes the Shapley
values; anything else with a `predict_proba` is explained by
`shap.PermutationExplainer`, which estimates them. Both are genuine SHAP. The
response carries `method`, `approximate` and the `shap` version, so an estimated
value is never presented as a computed one. A linear model is deliberately *not*
explained through its decision margin: the margin is a real number and SHAP will
happily decompose it, but it is a quantity no operator was ever shown, and
attributing it next to a probability that exists would answer a question nobody
asked.

A Shapley value is defined against a reference distribution, so the reference is
part of the fitted artifact rather than something chosen at explain time. The
training rows the artifact was fitted on are the background for a local
explanation, and the held-out rows Module 09 scored are the sample for a global
summary. An artifact with no reference sample is refused with
`no_reference_distribution`, because picking a distribution at explain time would
silently decide every number in the response.

Attributions are only stored if they account for the number they explain: base value
plus contributions must equal the stored probability within `1e-3`, and the residual
measured is reported as `attribution_error` rather than rounded away. An attribution
that does not add up to the probability is refused with
`attribution_does_not_reconcile`.

Every declared feature gets a row, including one whose contribution is exactly
zero. A list that dropped it would read as "this feature did not matter" when the
truth is "this feature was not looked at", and those are different sentences. A
feature the twin could not supply is marked `imputed` and reports **no** value: the
preprocessor put the training median there, which is a statistic of the training
rows and not a measurement of this pan.

`GET /models/{model_id}/versions/{version}/importance` is the model-evaluation half —
the mean absolute contribution per feature over the held-out rows, one row per
feature, ranked, with the row count actually used reported. It is a different claim
from a coefficient or a correlation and can disagree with both, so the response says
what was averaged, over what, and whether the contributions were estimated. The
summary is stored under a deterministic id, so the same model version gives the
same ranking and a stored figure can be quoted.

The store enforces one explanation per prediction, with a unique partial index on
`(prediction_id)` for `kind: "local"`. Re-running returns `200` and refreshes the
one row rather than accumulating a second answer to a question already answered;
a prediction that has none yet returns `201`. Global summaries are keyed by model
version and are not per-prediction rows.

Refusals, each naming the quantity that is missing rather than reporting a failure:
`no_such_prediction`, `no_such_model_version`, `explanation_not_stored`,
`artifact_unavailable`, `no_reference_distribution`, `model_reports_no_probability`,
`input_snapshot_mismatch`, `class_not_in_artifact`, `feature_mapping_unavailable`,
`attribution_does_not_reconcile`.

See [api.md](api.md) for the contract.

Module 12 turns what the other modules know into one of four actions, and the
position of the module is that **it holds no thresholds and no default action of
its own.** Every action it recommends comes from a rule written through
`POST /api/decisions/rules`, stored with a content hash and a version and
readable back. A default action would be a rule nobody wrote and nobody could
revise without invalidating every recommendation already issued, which is the
property that makes a recommendation an argument rather than a number.

The action vocabulary is closed at four: `HARVEST NOW`, `TRANSFER BRINE`,
`PROTECT / DRAIN`, `WAIT AND RECHECK`. A model contributes an estimate that a
criterion may read; it never contributes an action.

**Four kinds of evidence stay four kinds.** The twin state, the mass balance, the
stored model estimate and the rule's own matching are separate fields with
separate identifiers, and every entry in `inputs_used` names where its value came
from. They are never merged into one table, because a simulated salinity sitting
beside a measured one is exactly how a computed figure comes to be read as an
observation. This continues the distinction Module 11 already draws between a
probability, a contribution and a physics result: three different claims about a
pan, and a recommendation that reads all three must not flatten them.

**The physics is Module 06's, run and stored, not a copy of its arithmetic.** The
result carries the `run_id` it was read from, so the figure can be replayed from
the stored `SimulationRun`, and `is_ml: false` is present explicitly so no
consumer has to infer that no model was involved. The rainfall comes from the
twin's forecast unless the request names a what-if depth, and the source used is
recorded either way.

**The model estimate is read and never produced.** The engine looks up the latest
stored operational-risk prediction and copies it. It does not train, deploy or
score anything, because a model fitted for the first time by a request for advice
would make this module's own figures look like independent evidence. A rule that
declares no prediction variable gets no `ml_result` at all — attaching an
estimate the rule did not consult would dress a model answer as a rule's
reasoning.

**There is no confidence.** No recommendation-level number, no percentage, no
strength. A rule that matched is either decisive or it is not, and a number there
would be read alongside the model's own confidence and imply a likelihood nobody
computed. The panel renders the model's own figures inside the model's block and
nowhere else, and a test asserts that the only percentage anywhere on the screen
is the physics dilution.

**A recommendation without a model estimate is a complete answer**, not a partial
one. It carries the project's exact required label —
*Physics/rule-based recommendation — ML prediction unavailable.* — followed by
which of the three reasons applies, and the notice is stored on the record so the
wording is the same on every surface that shows it. The same reasoning keeps a
class-only model's missing probability at `null` with a reason beside it: `0%`
would be an assertion the model never made.

**A rule that cannot read its input withholds its fallback.** A fallback is
allowed to decide only from evidence it can actually see. When a criterion's
variable is missing, the engine refuses with `rule_undecided` rather than letting
the fallback absorb exactly the pans whose evidence is thinnest, since a
safe-looking default that fires precisely when the system knows least is the worst
place to put one.

**Rule versions are immutable and content-hashed.** Identical content is reused
rather than versioned again — a rule that reads the same as a stored one is the
same rule — and a changed criterion allocates the next version and records what
it supersedes. A criterion naming an uncatalogued variable, contradicting the
catalogue's unit, ordering a categorical variable, or giving a numeric variable a
string threshold is refused at write time, because a rule holding a comparison that
can never succeed reads as a decision the operator can rely on.

The four refusals — `no_twin_state`, `simulation_inputs_missing`,
`no_rule_defined`, `rule_undecided` — each name the thing that is missing rather
than reporting a failure, and all four describe a state of the system rather than a
mistake in the request, so none of them is worth retrying. The frontend shows one
fixed sentence per code and offers no retry button for any of them.

See [api.md](api.md) for the contract.

Module 15 records what a pan actually did, and grades the prediction against it.
`POST /api/outcomes/` takes the measurements only — forecast rainfall, actual
rainfall, pre- and post-event salinity and depth, the harvest outcome and an
optional operator action. It never accepts a predicted value, an error term or an
accuracy figure, so the numbers on the page cannot be asserted by the client that
asks for them.

The prediction is computed on the server by the Module 06 mass balance, and
three comparisons are stored per event:

| Comparison                     | Predicted from              | Compared against              |
| ------------------------------ | --------------------------- | ----------------------------- |
| `simulation_error`             | actual rainfall             | measured post-event salinity  |
| `forecast_simulation_error`    | forecast rainfall           | measured post-event salinity  |
| `rainfall_forecast_error`      | forecast rainfall           | actual rainfall               |

`POST` resolves and snapshots the four records the event is graded against — the
pan state before it, the forecast covering it, the `SimulationRun` behind the
prediction, and the recommendation in force. An explicit link must exist and
belong to the same pan, so an outcome cannot be graded against another pan's
state. The run is adopted only when its stored inputs match the event; otherwise
a fresh, reproducible run is persisted, so the prediction behind every error can
be re-derived later.

A measurement that was never taken stays `null`. An event with no recorded
post-event salinity is not comparable, and is excluded from every mean rather
than counted as a zero error — the difference between "the model was right" and
"we never looked". Percentage error against a zero actual is reported as `null`
with a reason, for the same reason: it is a ratio against a figure that does not
exist, not a 0% result.

`GET /api/outcomes/comparison` returns the mean absolute error, mean signed
error (bias), largest absolute error and the compared/not-comparable counts for
each of the three comparisons. The counts are part of the statistic, not
metadata: a mean over two of twenty events is meaningless alone, so the frontend
never renders one without saying what it excluded.

The operator side is a form on the pan detail page and a `Predicted vs actual`
page at `/predicted-vs-actual`, both of which read the same endpoints. A demo pan
shows neither, because a fabricated outcome would put invented errors into the
one place that exists to measure the model. When the backend is unreachable the
page says the comparison is unavailable rather than showing a plausible number,
and the counts are omitted rather than shown as zero.

See [api.md](api.md) for the contract.

Module 17 decides which model version is allowed to answer an operational
question, and makes that decision a recorded act rather than a side effect. A
version is created `experimental` by the training run, moves to `validated` only
when a stored evaluation report exists and a person has read it, and reaches
`deployed` only through a request that names the version, the actor and the
reason. The lifecycle runs forward one step at a time and `retired` is terminal.

**Retraining never replaces what is deployed.** A new run writes a new version in
`experimental`; the deployed version keeps answering until somebody displaces it
deliberately. Deploying over an incumbent is refused with a 409 that names the
incumbent and the exact request that would make the replacement deliberate, and
proceeding requires both `supersede: true` and a `supersede_reason`. When it does
proceed, the incumbent is retired with that reason recorded against it and a
pointer set to its successor, and both steps are appended to a history that is
never edited. At most one version per target holds `deployed`; the service refuses
a second and a unique partial index on the cluster refuses it too.

The module is also where a model version's whole basis is kept: dataset and
dataset version, training date, features, hyperparameters, split strategy, the
full target definition (including the risk rule's id and specification hash), and
Module 09's evaluation report with its metrics as readings rather than numbers. A
metric that was not computed arrives as `value: null` with its reason. It is not
filled in with `0.0` at this layer, because a filled cell reads as a measurement
nobody made.

The consequence for the rest of the system is the point of the module:
`POST /api/risk/predict` is answered by a version whose status is `deployed` and
by no other. There is no default model, no fallback to the newest or
best-scoring version, and no approximate answer — with nothing deployed, Module 10
refuses. The features are read from the pan's **current digital twin state**,
column for column as the deployed model declared them, so a caller cannot name a
model, hand it a number, or cause a row to be built any other way than the one the
model was fitted on. A column the twin cannot supply stays absent and is imputed by
the model's own preprocessor rather than substituted, and is reported as
`features_absent` so a reader can see how much of the row was real. The stored
prediction carries the serving `model_id`, `model_version`, `deployment_id` and
`deployed_at`, the class, the probability, and the input snapshot with its content
hash as `input_snapshot_id` — a figure that can be recomputed against the state it
claims to describe.

See [api.md](api.md) for the contract.

## Dependency order (research-flow)

Modules 1 → 4 establish identity and raw truth sources (auth, pans,
observations, weather). Modules 5 → 6 build the digital twin and its
deterministic simulator from that real data. Modules 7 → 9 curate datasets,
train models and evaluate them honestly. Modules 10 → 12 deliver risk,
explainability and decisions. Modules 13 → 16 close the operator loop
(dashboard, feedback, outcomes, validation) which supplies the ground truth
that re-trains the system in Module 17 (registry/retraining). Module 18
exports everything needed for the research paper. Module 19 records each
completed experiment itself — the append-only ledger whose every row is a
finished run with the reproducibility manifest that re-derives it.

Each router's `GET /` returns:

```json
{
  "module": "risk",
  "module_number": 10,
  "status": "not_implemented",
  "description": "...",
  "planned_endpoints": ["POST /api/risk/predict", "..."]
}
```
