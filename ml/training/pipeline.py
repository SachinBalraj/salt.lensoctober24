"""Module 8 - the research-layer entrypoint.

There is exactly one implementation of the training pipeline, and it lives in
``backend/app/ml/pipeline.py`` beside the models, the datasets and the registry it
reads and writes. This module is the command-line door onto it. It does not
reimplement anything: a second copy of a training pipeline is a second set of
conclusions about what the data supports, and the two would drift.

Usage, against a running Mongo holding real datasets::

    python -m ml.training.pipeline train <dataset_id> \\
        [--split chronological] [--test-size 0.2] [--seed 20260926] [--json]

What it prints is the reproducibility record: the dataset version and content
hash, the features, the target, the split, every candidate's held-out scores, the
selected algorithm and why, the library versions, the seed and the artifact path.

On refusal it prints the required sentence and the counts that failed, and exits
non-zero without writing anything::

    Insufficient labelled data for reliable model training.
      - only 12 labelled row(s); at least 80 are needed ...
      - class 'high' has 1 labelled row(s); at least 5 are needed ...

The refusal is a successful run of this program. It found the data cannot support
a model, which is the answer, and it did not invent one to have something to print.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

#: The backend is a sibling package, not an installed distribution, so it goes on
#: the path explicitly. Done here rather than at the call site because forgetting it
#: should produce one clear message from this module rather than a stack of
#: ImportErrors from somewhere deeper.
_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.ml.pipeline import (
    DEFAULT_RANDOM_SEED,
    DEFAULT_TEST_SIZE,
    InsufficientDataError,
)
from app.ml.splitting import DEFAULT_SPLIT_STRATEGY, SPLIT_STRATEGIES
from app.ml.sufficiency import INSUFFICIENT_DATA_MESSAGE


async def _train_in_loop(request: Any) -> Any:
    """Open the connection, then train, on one event loop.

    The order matters and the co-location matters more. Nothing has run the
    application's startup event in a standalone process, so there is no
    connection to borrow; and a Motor client is bound to the loop that built it,
    so a connection opened in a different ``asyncio.run`` is stranded on a closed
    loop and every read afterwards fails against a database that is genuinely
    reachable. The two steps therefore share a loop deliberately.
    """
    from app.api.deps import get_db
    from app.db.mongo import db
    from app.services.ml import MLService

    try:
        if not db.connected:
            await db.connect()
        return await MLService(get_db).train(request)
    finally:
        # Closed on the loop that opened it. A command-line process that keeps a
        # Mongo client alive does not exit when the work is done: the client's
        # monitoring threads stay up, so the command hangs after printing a
        # perfectly good refusal. Silent and easy to mistake for a slow query.
        await db.close()


def train_candidate(
    dataset_id: str,
    *,
    split_strategy: str = DEFAULT_SPLIT_STRATEGY,
    test_size: float = DEFAULT_TEST_SIZE,
    random_seed: int = DEFAULT_RANDOM_SEED,
    allow_random_forest: bool = False,
) -> Any:
    """Train from a stored dataset and return the registered model version.

    Raises :class:`~app.ml.pipeline.InsufficientDataError` when the data cannot
    support a model. That is the designed outcome, not a crash, and the caller
    turns it into the refusal sentence.
    """
    from app.services.ml import TrainRequest

    return asyncio.run(
        _train_in_loop(
            TrainRequest(
                dataset_id=dataset_id,
                split_strategy=split_strategy,
                test_size=test_size,
                random_seed=random_seed,
                allow_random_forest=allow_random_forest,
            )
        )
    )


def evaluate_candidate(model_dir: str) -> None:
    """Not implemented. This is Module 9's work and Module 8 does not guess at it.

    Held-out evaluation happens here, inside a training run, and its results are
    reported with the model. Scoring an already-fitted artifact on fresh data is a
    separate question -- it needs a dataset the model was not fitted to, and a
    decision about what to do with a worse answer -- so it is left to Module 9
    rather than approximated.
    """
    raise NotImplementedError(
        "Scoring a saved model against fresh data is Module 9's work. Module 8 "
        "reports held-out scores from the run that fitted the model and does not "
        "approximate a later evaluation. A number produced without deciding which "
        "data counts as fresh would not be a measurement of anything."
    )


def _report(record: Any) -> str:
    """The reproducibility record, as text."""
    parameters = record.parameters
    lines = [
        f"model        {record.model_id} {record.version}  ({record.status.value})",
        f"target       {record.target.value}",
        f"selected     {record.algorithm}",
        f"why          {parameters.get('selection_reason', '')}",
        "",
        "held-out scores",
    ]
    scores = parameters.get("candidate_metrics", {})
    for algorithm, values in sorted(
        scores.items(), key=lambda item: -item[1].get("f1_macro", 0.0)
    ):
        marker = "  <- selected" if algorithm == record.algorithm else ""
        lines.append(
            f"  {algorithm:<22} f1_macro={values.get('f1_macro', 0):.3f} "
            f"balanced_accuracy={values.get('balanced_accuracy', 0):.3f}"
            f"  accuracy={values.get('accuracy', 0):.3f}{marker}"
        )
    lines += [
        "",
        "provenance",
        f"  dataset     {record.dataset_id} v{record.dataset_version}",
        f"  content     {record.dataset_content_hash}",
        f"  split       {record.split_strategy}",
        f"  seed        {record.random_seed}",
        f"  features    {len(record.feature_names)} declared by the manifest",
        f"  trained at  {record.trained_at.isoformat()}",
        f"  duration    {record.training_duration_seconds:.3f}s",
        "  libraries   "
        + ", ".join(f"{name} {version}" for name, version in record.library_versions.items()),
        f"  artifact    {record.artifact_path}",
    ]
    return "\n".join(lines)


def _refusal(exc: InsufficientDataError) -> str:
    """The refusal, with the counts that caused it.

    The sentence first and alone on its own line, because that is the line a caller
    or a script matches on. The reasons follow as a list beneath it.
    """
    lines = [INSUFFICIENT_DATA_MESSAGE]
    lines += [f"  - {reason}" for reason in exc.reasons]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code rather than calling ``exit``."""
    parser = argparse.ArgumentParser(
        prog="ml.training.pipeline",
        description=(
            "Train a classification model from a stored dataset. Refuses, and "
            "writes nothing, when the data cannot support one."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    trainer = subparsers.add_parser("train", help="Train from a stored dataset.")
    trainer.add_argument("dataset_id", help="The stored dataset to fit.")
    trainer.add_argument(
        "--split",
        default=DEFAULT_SPLIT_STRATEGY,
        choices=list(SPLIT_STRATEGIES),
        help=(
            "How the held-out rows are chosen. 'chronological' trains on the "
            "earliest rows and tests on the most recent; 'grouped_by_pan' holds "
            "out whole pans; 'per_pan_chronological' cuts time within each pan and "
            "does not evaluate an unseen pan."
        ),
    )
    trainer.add_argument(
        "--test-size",
        type=float,
        default=DEFAULT_TEST_SIZE,
        help="Fraction of rows held out for evaluation.",
    )
    trainer.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_RANDOM_SEED,
        help="Seed for every stochastic step. Recorded on the model version.",
    )
    trainer.add_argument(
        "--allow-random-forest",
        action="store_true",
        help=(
            "Permit Random Forest to be selected. It is fitted and scored either "
            "way; this only allows it to win."
        ),
    )
    trainer.add_argument(
        "--json", action="store_true", help="Print the record as JSON."
    )

    arguments = parser.parse_args(argv)

    if arguments.command == "train":
        # Imported here rather than at the top: the refusal types live in the
        # service layer, and importing that at module scope would pull the whole
        # application in for a command that may only need to print usage.
        from app.services.ml import (
            DatasetDriftError,
            LeakageDetectedError,
            UnsupportedTargetError,
        )

        try:
            record = train_candidate(
                arguments.dataset_id,
                split_strategy=arguments.split,
                test_size=arguments.test_size,
                random_seed=arguments.seed,
                allow_random_forest=arguments.allow_random_forest,
            )
        except InsufficientDataError as exc:
            # Exit 2, not 1: this is a refusal with a reason, distinct from the
            # program failing. A caller can tell "the data will not do" from
            # "something broke" by the code alone.
            print(_refusal(exc), file=sys.stderr)
            return 2
        except (DatasetDriftError, LeakageDetectedError, UnsupportedTargetError) as exc:
            # The same shape as the refusal above and a different code, because
            # these are different problems with different fixes: one needs a
            # rebuild, one needs a feature removed, one needs a different target.
            # A traceback would bury the sentence that says which, and the point
            # of a command-line door is that the message survives being piped.
            print(
                f"\n{exc}\n",
                file=sys.stderr,
            )
            return 3
        if arguments.json:
            print(json.dumps(record.model_dump(mode="json"), indent=2, default=str))
        else:
            print(_report(record))
        return 0

    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
