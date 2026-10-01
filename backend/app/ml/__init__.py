"""Module 8 - the machine-learning experiment pipeline.

Four concerns, deliberately separated so each can be tested and reasoned about on
its own:

``preprocessing``
    Turning rows into a feature matrix, fitted on the training rows only.
``sufficiency``
    The single place that decides whether there is enough real data to train on,
    and the fixed sentence returned when there is not.
``splitting``
    How the held-out rows are chosen, and what each strategy does and does not
    promise.
``candidates``
    The four algorithms, and the rule that Random Forest is never selected
    automatically.
``pipeline``
    The run itself, in an order chosen so that nothing is fitted before the data
    has been checked and nothing is computed from held-out rows before the split.

The governing rule across all of it: no data is ever invented to make a model
possible.
"""

from app.ml.candidates import (
    CANDIDATE_ALGORITHMS,
    NOT_AUTO_SELECTABLE,
    candidate_specs,
    select_model,
)
from app.ml.pipeline import (
    DEFAULT_RANDOM_SEED,
    DEFAULT_TEST_SIZE,
    InsufficientDataError,
    TrainingOutcome,
    train,
)
from app.ml.preprocessing import (
    NON_FEATURE_COLUMNS,
    apply_preprocessing,
    fit_preprocessing,
)
from app.ml.splitting import (
    DEFAULT_SPLIT_STRATEGY,
    SPLIT_STRATEGIES,
    SplitStrategy,
    make_split,
)
from app.ml.sufficiency import (
    INSUFFICIENT_DATA_MESSAGE,
    SufficiencyReport,
    check_sufficiency,
)

__all__ = [
    "CANDIDATE_ALGORITHMS",
    "DEFAULT_RANDOM_SEED",
    "DEFAULT_SPLIT_STRATEGY",
    "DEFAULT_TEST_SIZE",
    "INSUFFICIENT_DATA_MESSAGE",
    "NON_FEATURE_COLUMNS",
    "NOT_AUTO_SELECTABLE",
    "SPLIT_STRATEGIES",
    "InsufficientDataError",
    "SplitStrategy",
    "SufficiencyReport",
    "TrainingOutcome",
    "apply_preprocessing",
    "candidate_specs",
    "check_sufficiency",
    "fit_preprocessing",
    "make_split",
    "select_model",
    "train",
]
