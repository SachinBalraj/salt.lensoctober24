"""Module 8 - the preprocessing pipeline, as a fitted and persisted object.

Everything a model needs to turn a Module 07 row into a feature vector lives here,
and the whole thing is one scikit-learn ``Pipeline`` so it can be fitted once and
saved with the model. That is not tidiness, it is the mechanism that prevents the
most common way a model reports a number it has no right to report: statistics
computed over the held-out rows.

Three decisions carry most of the weight.

**Imputation is fitted on the training rows only.** A median is a number derived
from data. Computing it over the whole dataset puts held-out information into the
training features, which is leakage with a preprocessing-shaped disguise, and it
is the single easiest mistake to make in a pipeline like this one. The same goes
for the one-hot category list and the scaler's means. All of it is learned inside
``fit`` on the training slice, and the artifact carries what it learned.

**A missing value is not a zero.** An unrecorded rainfall is not a dry hour, and
filling it with 0 would tell the model the pan was measured and found dry. The
imputers are fitted on observed values and record which columns they had to
invent, and the fitted pipeline exposes that, so "this column was 60% imputed"
is a visible fact about the run rather than something a reader has to infer from
the absence of a warning.

**Scaling is applied to every numeric column, not only the ones that need it.**
Logistic regression is the reason -- its decision surface is a linear combination
of scaled inputs, so a feature measured in millimetres and a feature measured in
g/L contribute on incomparable terms. Trees do not care, and applying it to them
anyway keeps the four candidates looking at the same numbers, so a difference
between them is a difference about the model and not about the preprocessing.

Categorical columns are one-hot encoded with ``handle_unknown="ignore"``. A
category seen only in the held-out rows encodes as all-zeros rather than raising:
an unencountered category is a fact about the data, and refusing to predict
because of it would be a worse failure than encoding it as absent. The fitted
pipeline keeps the training category list, so what it did with it is inspectable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from app.models.enums import FeatureKind

#: Columns never fed to a model, whatever the caller selected.
#:
#: Row keys and the anchor instant are excluded because a model given an
#: identifier memorises the entity rather than learning from it, and then scores
#: well on the same entities it memorised. ``target_*`` is excluded because it
#: describes the measurement the label was drawn from: feeding it back in hands
#: the model the answer. The ``risk_*`` columns are the sharpest of these -- they
#: record which criteria fired and assigned which class, so a rule-derived label
#: fed with its own provenance is the label itself, copied.
NON_FEATURE_COLUMNS: frozenset[str] = frozenset(
    {
        "row_id",
        "pan_id",
        "anchor_timestamp",
        "target_measured_at",
        "target_source_id",
        "target_source_collection",
        "is_labelled",
        "label",
        "risk_definition_id",
        "risk_definition_version",
        "risk_specification_hash",
        "risk_label_basis",
        "risk_source_variables",
        "risk_matched_criteria",
        "risk_unlabelled_reason",
    }
)

#: A column is treated as categorical when the catalogue says so, and never
#: guessed from its values. Inferring a type from data is how a column that
#: happens to hold only integers this month becomes numeric next month, and the
#: model fitted on the first reading silently stops matching the second.
NUMERIC = "numeric"
CATEGORICAL = "categorical"


def _kind_value(kind: Any) -> str:
    """Normalise a declared kind to ``"numeric"`` or ``"categorical"``.

    Accepts the catalogue's :class:`FeatureKind` enum as well as the plain
    strings, so a caller holding catalogue definitions can pass them straight
    through without first mapping them into this module's vocabulary.
    """
    if isinstance(kind, FeatureKind):
        return NUMERIC if kind is FeatureKind.NUMERIC else CATEGORICAL
    text = str(kind)
    if text.endswith("FeatureKind."):
        text = text.split(".")[-1]
    return text


def classify_columns(
    feature_names: Sequence[str], kinds: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """Split selected features into numeric and categorical by declared kind.

    A feature whose kind the catalogue does not state is an error rather than a
    guess. Assuming numeric because the values happen to be numbers is how a
    categorical column ends up mean-imputed, which invents an ordering between
    categories that was never stated.
    """
    numeric: list[str] = []
    categorical: list[str] = []
    for name in feature_names:
        declared = kinds.get(name)
        if declared is None:
            raise ValueError(
                f"Feature {name!r} has no declared kind in the feature catalogue, so "
                "the pipeline will not guess one. Declaring it numeric or "
                "categorical is what makes the encoding a decision rather than an "
                "accident."
            )
        kind = _kind_value(declared)
        if kind == NUMERIC:
            numeric.append(name)
        elif kind == CATEGORICAL:
            categorical.append(name)
        else:
            raise ValueError(
                f"Feature {name!r} is declared {declared!r}, which is neither "
                "numeric nor categorical. The pipeline encodes only those two and "
                "will not guess a third."
            )
    return numeric, categorical


@dataclass(frozen=True)
class PreprocessingReport:
    """What the fitted pipeline had to do to the data, stated plainly.

    Recorded on the run so a reader can see the cost of the model in the shape of
    its inputs: how much had to be invented, what the numeric columns were rescaled
    from, and how wide the one-hot encoding ended up.
    """

    numeric_columns: tuple[str, ...]
    categorical_columns: tuple[str, ...]
    imputed_numeric: dict[str, float] = field(default_factory=dict)
    imputed_categorical: dict[str, str] = field(default_factory=dict)
    encoded_categories: dict[str, list[str]] = field(default_factory=dict)
    scaled_means: dict[str, float] = field(default_factory=dict)
    scaled_stds: dict[str, float] = field(default_factory=dict)
    output_feature_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "numeric_columns": list(self.numeric_columns),
            "categorical_columns": list(self.categorical_columns),
            "imputed_numeric": dict(self.imputed_numeric),
            "imputed_categorical": dict(self.imputed_categorical),
            "encoded_categories": {
                name: list(values) for name, values in self.encoded_categories.items()
            },
            "scaled_means": dict(self.scaled_means),
            "scaled_stds": dict(self.scaled_stds),
            "output_feature_count": self.output_feature_count,
        }


def build_preprocessor(
    numeric_columns: Sequence[str],
    categorical_columns: Sequence[str],
) -> ColumnTransformer:
    """Construct the column transformer, unfitted.

    Separated from fitting so the shape of the pipeline is readable without
    reading the fitting code, and so the same transformer is rebuilt identically
    at predict time from the saved artifact.
    """
    numeric = Pipeline(
        steps=[
            # "median" rather than "mean": a salinity reading skewed by one
            # instrument fault should not drag the centre of the distribution.
            ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scale", StandardScaler()),
        ]
    )
    transformers: list[tuple[str, Any, list[str]]] = []
    if numeric_columns:
        transformers.append(("numeric", numeric, list(numeric_columns)))
    if categorical_columns:
        categorical = Pipeline(
            steps=[
                (
                    "impute",
                    SimpleImputer(
                        strategy="most_frequent", keep_empty_features=True
                    ),
                ),
                # A category present only in held-out rows encodes as all zeros.
                # That is a fact about the data, not a reason to refuse to predict.
                (
                    "encode",
                    OneHotEncoder(
                        handle_unknown="ignore",
                        sparse_output=False,
                        dtype=np.float64,
                    ),
                ),
            ]
        )
        transformers.append(("categorical", categorical, list(categorical_columns)))
    if not transformers:
        raise ValueError(
            "No usable features were selected, so there is nothing to preprocess. "
            "A model fitted on no columns would return a constant."
        )
    # sparse_threshold=0 keeps the output dense: every candidate here takes dense
    # input, and a sparse frame would have to be converted back for two of them.
    return ColumnTransformer(
        transformers=transformers,
        remainder="drop",
        sparse_threshold=0.0,
        verbose_feature_names_out=False,
    )


def fit_preprocessing(
    frame: pd.DataFrame,
    numeric_columns: Sequence[str],
    categorical_columns: Sequence[str],
) -> tuple[Pipeline, PreprocessingReport]:
    """Fit preprocessing on the training rows and report what it learned.

    The caller must pass training rows only. Nothing here can tell whether it was
    given training rows, so the argument name and this docstring are the guard:
    a median fitted over train and test is a quiet leak that no metric will
    reveal.
    """
    preprocessor = build_preprocessor(numeric_columns, categorical_columns)
    matrix = preprocessor.fit_transform(frame)

    imputed_numeric: dict[str, float] = {}
    scaled_means: dict[str, float] = {}
    scaled_stds: dict[str, float] = {}
    if numeric_columns:
        numeric_step = preprocessor.named_transformers_["numeric"]
        values = np.asarray(numeric_step.named_steps["impute"].statistics_)
        for name, value in zip(numeric_columns, values):
            # A float('nan') statistic means the column was entirely absent from
            # the training rows, so there was nothing to learn a centre from.
            if value is not None and not (isinstance(value, float) and np.isnan(value)):
                imputed_numeric[name] = float(value)
        scaler = numeric_step.named_steps["scale"]
        for name, mean, std in zip(
            numeric_columns,
            np.asarray(scaler.mean_).ravel(),
            np.asarray(scaler.scale_).ravel(),
        ):
            scaled_means[name] = float(mean)
            scaled_stds[name] = float(std)

    imputed_categorical: dict[str, str] = {}
    encoded_categories: dict[str, list[str]] = {}
    if categorical_columns:
        categorical_step = preprocessor.named_transformers_["categorical"]
        statistics = np.asarray(
            categorical_step.named_steps["impute"].statistics_, dtype=object
        )
        for name, value in zip(categorical_columns, statistics):
            if isinstance(value, str) and value:
                imputed_categorical[name] = value
        categories = categorical_step.named_steps["encode"].categories_
        for name, values in zip(categorical_columns, categories):
            encoded_categories[name] = [str(value) for value in values]

    report = PreprocessingReport(
        numeric_columns=tuple(numeric_columns),
        categorical_columns=tuple(categorical_columns),
        imputed_numeric=imputed_numeric,
        imputed_categorical=imputed_categorical,
        encoded_categories=encoded_categories,
        scaled_means=scaled_means,
        scaled_stds=scaled_stds,
        output_feature_count=int(matrix.shape[1]),
    )
    # sklearn's own Pipeline, so the artifact is the same object a caller can
    # .predict() with and there is no second code path to keep in step.
    pipeline = Pipeline(steps=[("preprocess", preprocessor)])
    return pipeline, report


def apply_preprocessing(
    pipeline: Pipeline, frame: pd.DataFrame
) -> np.ndarray:
    """Transform rows with an already-fitted pipeline.

    Separate from fitting so that using a model on new data cannot accidentally
    re-learn imputation statistics from those new rows.
    """
    return np.asarray(pipeline.named_steps["preprocess"].transform(frame))


def library_versions() -> dict[str, str]:
    """The versions that determine what a fitted artifact means.

    A joblib file fitted by one scikit-learn version is not reliably loadable by
    another, and a pipeline that reorders its categories between versions
    produces different predictions from the same rows. Recorded so a reader knows
    which combination produced the numbers beside them.
    """
    return {
        "scikit-learn": sklearn.__version__,
        "numpy": np.__version__,
        "pandas": pd.__version__,
    }


__all__ = [
    "CATEGORICAL",
    "NON_FEATURE_COLUMNS",
    "NUMERIC",
    "PreprocessingReport",
    "apply_preprocessing",
    "build_preprocessor",
    "classify_columns",
    "fit_preprocessing",
    "library_versions",
]
