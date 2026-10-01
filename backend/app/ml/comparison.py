"""Module 09 - the comparison table, assembled from evaluations that already exist.

The arithmetic happened in :mod:`app.ml.evaluation` and the resampling happened in
:mod:`app.ml.cross_validation`. This file does neither. It takes finished
:class:`ClassificationEvaluation` objects and lays them out in the six columns the
comparison asks for, which makes it the smallest and the least interesting part of
the module -- and the part where it is easiest to quietly lie.

**Every cell is a :class:`MetricReading`, not a float.** Not a float with a null,
not a float defaulted to zero, not a string saying "n/a". A float would make an
absent metric and a metric of zero the same value, and the table is the object most
likely to be quoted, screenshotted and pasted into a paper. The reading travels
with its reason, so a cell that says "not computed" says why in the same breath.

**No row is called "best".** The table carries a ``selected`` boolean and the
*documented* criterion that produced it -- Module 8's macro-F1 rule, unchanged --
so a reader can check the selection against the scores rather than taking a label
on trust. Ranking four numbers and calling the top one "best" is not the same act
as stating a rule that decides, and only one of the two can be audited. A table
whose winner is a word rather than a rule is a table nobody can disagree with.

**Comparability is computed, not assumed.** Two rows are only in the same
comparison if they were scored on the same rows, and the table says so when they
were not. Across model versions, a later version's figures may come from a rebuilt
dataset or a different held-out fraction, and a table that ranked them anyway would
be reporting a difference in the rows as a difference in the models. The
``comparable`` block states which rows share a denominator and which do not, and
``evaluation_set`` sits on every row so the denominator is never more than one
field away.

**Cross-validated figures are kept out of the single-split columns.** They answer a
different question and are exposed in their own per-row ``cross_validation`` block.
Merging a five-fold mean and a one-split score into one column would produce a table
in which the largest number in a row is not a measurement of the same thing as its
neighbours.

Random Forest appears in the table with ``auto_selectable: false`` and the reason.
It was fitted and it was scored, so hiding it would make the record incomplete, and
showing it unmarked would present it as being in the running.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.ml.candidates import CANDIDATE_ALGORITHMS, NOT_AUTO_SELECTABLE
from app.ml.cross_validation import AlgorithmCrossValidation
from app.models.evaluation import (
    ClassificationEvaluation,
    CrossValidationReport,
    MetricReading,
)

#: The six columns the comparison asks for, in the order it asks for them. Exposed
#: as a constant because a table that grows a seventh column is a table whose
#: columns and whose contract have drifted apart, and the frontend renders from
#: this list rather than from a hand-written header row.
COMPARISON_COLUMNS: tuple[str, ...] = (
    "accuracy",
    "precision",
    "recall",
    "f1",
    "roc_auc",
    "pr_auc",
)

#: The human-readable header for each column. Separate from the key because the key
#: is what the readings are looked up by and the header is what a reader sees, and
#: conflating them is how a display name ends up being used as a lookup key.
COLUMN_TITLES: dict[str, str] = {
    "accuracy": "Accuracy",
    "precision": "Precision (macro)",
    "recall": "Recall (macro)",
    "f1": "F1 (macro)",
    "roc_auc": "ROC-AUC",
    "pr_auc": "PR-AUC",
}

#: Why a cell that has a number may still be a convention rather than a
#: measurement. Restated on the table because a convention applied inside a
#: per-class calculation is invisible from a macro column, and a macro F1 of 0.60
#: that is half convention and half measurement should not read like a measured
#: 0.60.
CONVENTION_KEY = "conventions_in_force"

#: The selection rule, quoted from Module 8 rather than restated. Restating a rule
#: in a second file is how two files come to disagree about what selected a model,
#: and the disagreement is invisible until someone reads both.
SELECTION_CRITERION = (
    "The selected row is the one Module 8's rule selected: the highest macro F1 "
    "among the automatically selectable candidates, ties broken on balanced accuracy "
    "and then on the fixed candidate order ("
    + ", ".join(CANDIDATE_ALGORITHMS)
    + "). random_forest is fitted and scored on every run and is never selected "
    "automatically. No row is labelled 'best', because a rule that decides can be "
    "checked against the scores beside it and a superlative cannot."
)

#: What a comparison will not do, stated in the payload so a client reading only
#: the response still gets it.
COMPARISON_DOES_NOT_DO = (
    "This table does not rank by any score of its own, does not call a row best, "
    "and does not fill an absent metric with zero. Cells carry the reading and the "
    "reason the reading is absent; cross-validated figures are reported separately "
    "from the single-split columns because they are measured on different rows."
)


def _algorithm_order(algorithm: str) -> int:
    """Position in the fixed candidate order, or last for anything unknown.

    The order Module 8 reports candidates in is the order the tie-break rule uses,
    so reusing it here means the table's row order and the selection's tie-break
    cannot come apart.
    """
    if algorithm in CANDIDATE_ALGORITHMS:
        return CANDIDATE_ALGORITHMS.index(algorithm)
    return len(CANDIDATE_ALGORITHMS)


def _selection_note(algorithm: str) -> str:
    """Why a row is or is not eligible to be the selected one."""
    if algorithm in NOT_AUTO_SELECTABLE:
        return (
            "Fitted and scored on every run and shown in this table, but never "
            "selected automatically: on these data volumes an ensemble of deep trees "
            "wins a held-out score by memorising, and making that the default would "
            "turn a warning about dataset size into a deployment. It can be promoted "
            "deliberately, and the promotion is recorded on the model version."
        )
    return "Eligible for selection under the documented macro-F1 rule."


def _cv_cell(
    report: CrossValidationReport | None, algorithm: str
) -> dict[str, Any] | None:
    """The cross-validated figures for one candidate, or None if there are none.

    Returned as a nested block rather than folded into the single-split columns,
    because a five-fold mean of accuracy and a single-split accuracy are two
    measurements of two different row sets and belong in two different columns.
    """
    if report is None or not report.performed:
        return None
    entry: AlgorithmCrossValidation | None = report.per_algorithm.get(algorithm)
    if entry is None:
        return None
    return {
        "performed": True,
        "method": report.method,
        "n_splits": report.n_splits,
        "shuffled": report.shuffled,
        "evaluates_unseen_pans": report.evaluates_unseen_pans,
        "folds_run": len(entry.folds),
        "auto_selectable": entry.auto_selectable,
        "selection_note": entry.selection_note,
        "aggregate": {
            metric: aggregate.as_dict()
            for metric, aggregate in entry.aggregate.items()
            if metric in COMPARISON_COLUMNS
        },
    }


def build_comparison(
    *,
    candidate_evaluations: Mapping[str, ClassificationEvaluation],
    selected_algorithm: str | None,
    cross_validation: CrossValidationReport | None = None,
    model_id: str | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    """Lay the candidates out as the comparison table.

    ``candidate_evaluations`` is keyed by algorithm and iterated in Module 8's
    candidate order rather than in the order it happens to arrive, so two runs
    produce the same row order and a table can be compared with a table.

    ``selected_algorithm`` is recorded as a boolean per row and the criterion that
    chose it travels with the table. It is never used to reorder, filter, or
    highlight-by-superlative: the table shows every candidate that was fitted.
    """
    if not candidate_evaluations:
        raise ValueError(
            "A comparison needs at least one evaluation. Building a table from an "
            "empty map would produce a table with no rows, which reads as 'nothing "
            "to report' rather than 'nothing was evaluated'."
        )

    ordered = sorted(candidate_evaluations, key=_algorithm_order)
    rows: list[dict[str, Any]] = []
    evaluation_sets: set[str] = set()
    row_counts: set[int] = set()
    variants: dict[str, set[str]] = {column: set() for column in COMPARISON_COLUMNS}
    conventions: set[str] = set()

    for algorithm in ordered:
        evaluation = candidate_evaluations[algorithm]
        metrics = evaluation.headline()
        evaluation_sets.add(evaluation.evaluation_set)
        row_counts.add(evaluation.evaluated_rows)
        for column, reading in metrics.items():
            if reading.computed and reading.variant:
                variants[column].add(reading.variant)
        conventions.update(evaluation.notes)

        rows.append(
            {
                "model": algorithm,
                "algorithm": algorithm,
                "model_id": model_id,
                "version": version,
                "selected": algorithm == selected_algorithm,
                "auto_selectable": algorithm not in NOT_AUTO_SELECTABLE,
                "selection_note": _selection_note(algorithm),
                "evaluated_rows": evaluation.evaluated_rows,
                "evaluation_set": evaluation.evaluation_set,
                "classes": list(evaluation.classes),
                "class_distribution": dict(evaluation.class_distribution),
                "metrics": {column: reading.as_dict() for column, reading in metrics.items()},
                "cross_validation": _cv_cell(cross_validation, algorithm),
            }
        )

    # Cross-model comparability. Rows sharing a denominator can be ranked against
    # each other; rows that do not, cannot, and saying so is the difference between
    # a comparison and a juxtaposition.
    same_rows = len(row_counts) == 1
    same_set = len(evaluation_sets) == 1
    caveats: list[str] = []
    if not same_rows:
        counts = ", ".join(f"{count} row(s)" for count in sorted(row_counts))
        caveats.append(
            "These rows were scored on different numbers of rows ("
            + counts
            + "), so a difference between them may be a difference in the rows "
            "rather than in the models."
        )
    if not same_set:
        caveats.append(
            "These rows were scored on different row sets ("
            + "; ".join(sorted(evaluation_sets))
            + "), so they are not directly comparable."
        )
    for column, seen in variants.items():
        if len(seen) > 1:
            caveats.append(
                f"The {COLUMN_TITLES[column]} column mixes variants: "
                + ", ".join(sorted(seen))
                + ". A one-vs-rest average is a different quantity from a two-class "
                "curve, and the difference between such a row and its neighbour is "
                "not a modelling result."
            )

    cv_performed = bool(cross_validation and cross_validation.performed)

    return {
        "columns": list(COMPARISON_COLUMNS),
        "column_titles": dict(COLUMN_TITLES),
        "rows": rows,
        "row_count": len(rows),
        "selected_algorithm": selected_algorithm,
        "selection_criterion": SELECTION_CRITERION,
        "average_precision_note": COLUMNS_NOTE,
        "cross_validation_performed": cv_performed,
        "cross_validation_note": (
            None
            if cv_performed
            else "Cross-validation was not performed for this evaluation. "
            + (
                cross_validation.reason
                if cross_validation is not None and cross_validation.reason
                else "The report was not run."
            )
        ),
        "comparable": {
            "same_evaluation_set": same_set,
            "same_row_count": same_rows,
            "evaluation_set": min(evaluation_sets) if same_set else None,
            "evaluated_rows": min(row_counts) if same_rows else None,
            "caveats": caveats,
        },
        CONVENTION_KEY: sorted(conventions),
        "does_not": COMPARISON_DOES_NOT_DO,
    }


#: Stated once on the table because the column header cannot. "PR-AUC" is a
#: different computation for a two-class target (average precision over the
#: positive class) than for a multi-class one (a macro one-vs-rest average), and a
#: header that says only the abbreviation invites a reader to treat the two as the
#: same number.
COLUMNS_NOTE = (
    "Precision, recall and F1 are macro-averaged. PR-AUC is average precision over "
    "the positive class for a two-class target and a macro one-vs-rest average for a "
    "multi-class one; each reading's `variant` field says which, and ROC-AUC is only "
    "reported for a two-class problem, with a multi-class run's one-vs-rest figure "
    "carrying variant 'one_vs_rest_macro' so it is never read as a two-class curve."
)


def flatten_readings(
    comparison: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """One flat record per row, for the CSV export.

    The table is a nested structure because a chart and a table want it nested, and
    a CSV wants it flat. Flattening happens here, once, rather than in the router,
    so the column order and the names in the export cannot drift from the ones the
    table renders. An absent metric is written as an empty cell, never as 0 -- the
    same rule the API applies, in a format where it is easier to break.
    """
    records: list[dict[str, Any]] = []
    for row in comparison.get("rows", []):
        metrics = row.get("metrics", {})
        record: dict[str, Any] = {
            "model": row.get("model"),
            "model_id": row.get("model_id"),
            "version": row.get("version"),
            "selected": row.get("selected"),
            "auto_selectable": row.get("auto_selectable"),
            "evaluated_rows": row.get("evaluated_rows"),
            "evaluation_set": row.get("evaluation_set"),
        }
        for column in comparison.get("columns", []):
            reading: MetricReading | dict[str, Any] | None = metrics.get(column)
            if isinstance(reading, MetricReading):
                record[column] = reading.value
                record[f"{column}_computed"] = reading.computed
                record[f"{column}_variant"] = reading.variant
                record[f"{column}_reason"] = reading.reason
            elif isinstance(reading, dict):
                record[column] = reading.get("value")
                record[f"{column}_computed"] = reading.get("computed")
                record[f"{column}_variant"] = reading.get("variant")
                record[f"{column}_reason"] = reading.get("reason")
            else:
                record[column] = None
                record[f"{column}_computed"] = False
                record[f"{column}_variant"] = None
                record[f"{column}_reason"] = (
                    "The comparison row carries no reading for this metric."
                )
        records.append(record)
    return records


__all__ = [
    "COLUMNS_NOTE",
    "COLUMN_TITLES",
    "COMPARISON_COLUMNS",
    "COMPARISON_DOES_NOT_DO",
    "CONVENTION_KEY",
    "SELECTION_CRITERION",
    "build_comparison",
    "flatten_readings",
]
