# ML research pipeline (higher than API sandbox)

`ml/` is the research engineering layer. Module 08's training pipeline is
implemented: every entrypoint that produces a model runs the same core as the
API, shares the same provenance, and refuses to fabricate when the data will
not support a model.

| Path            | Purpose                                                        |
| --------------- | -------------------------------------------------------------- |
| `features/`     | feature-engineering transforms (numpy/pandas, safe by pan)     |
| `training/`     | pipeline entrypoints: split → train → register artifact        |
| `notebooks/`    | experiments + plots for the paper (git-ignored artifacts)      |
| `models/`       | joblib artifacts + registry metadata (git-ignored; `.gitkeep`) |
| `README.md`     | you are here                                                  |

Policy: never fake inputs, predictions, SHAP values, or metrics. Train only
on stored datasets (`data/processed` imports via `DatasetService`); evaluate
only on a held-out split from real data; export held-out scores from the run
that fitted the model, not from constants. No synthetic rows, labels,
oversampling, augmentation, or weak fallback models.

`ml/training/pipeline.py` is the command-line door onto the single
implementation in `backend/app/ml/pipeline.py`. It prints a reproducibility
record (dataset hash/version, target, features, split, every candidate's
held-out scores, the selected algorithm and why, seed, library versions, training
duration, and artifact path). On refusal it prints

```
Insufficient labelled data for reliable model training.
  - <reason 1>
  - <reason 2>
  ...
```

and exits with code `2` (refused, not failed) without writing any artifact or
registry row.

## The risk label is external configuration, not a constant here

A risk model is fitted to a severity vocabulary, and that vocabulary has to be
inspectable and revisable independently of any model fitted to it. It is
therefore data, written through Module 10 (`/api/risk`), not a threshold in this
directory. There is no `HIGH_THRESHOLD = 0.8` anywhere in `ml/`, and adding one
would reintroduce exactly the problem the module exists to solve: a model whose
label boundaries cannot be cited, revised or versioned alongside it.

The practical consequence for this layer: a risk model's target column is
whatever `label_column` the stored definition names (default
`label_operational_risk`), and the rule that produced it is recorded on every row
as `risk_definition_id`, `risk_definition_version`, `risk_specification_hash`,
`risk_label_basis`, `risk_source_variables` and `risk_matched_criteria`. Those
columns are in the Module 07 export, so a training run can assert that it fitted
the rule it thinks it fitted.

Before fitting anything to a rule-derived dataset, read the distribution that
comes back on the build report (`report.risk_distribution`) or from
`POST /api/risk/definitions/{definition_id}/distribution`. It reports the class
balance, which declared classes were never observed, and whether the split
supports training at all — the last point at which a 97/2/1 distribution can be
noticed before an accuracy figure is quoted as though it meant something. A
retrospective rule (`report.risk_distribution.label_basis ==
"retrospective"`) describes what happened rather than what was knowable at the
anchor and cannot be used to forecast the event it is graded against.

There is also no `predict` to import. Module 10 refuses it with a 501 because
nothing is trained; a score produced by this directory before a trained model
exists would be a number with no model behind it.
