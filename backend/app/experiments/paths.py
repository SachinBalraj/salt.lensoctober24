"""Where the experiment registry lives on disk.

Files, not a collection, and that is a deliberate choice for a *frozen*
experiment.

A Mongo collection is the right home for a running system's records: it is
queryable, transactional and always available. An experiment is not a running
system's record, it is a sealed artefact. It has to be diffable, reviewable in a
pull request, hashable, and readable without a running cluster or a credential
five years from now — because a paper's result has to be checkable by a reviewer
who has no access to anything of the author's. Plain JSON under
``experiments/`` gives all of that; a collection gives none of it.

The location is overridable so a test, a CI job or a second frozen experiment can
work on its own copy without touching the committed one.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Overrides the registry root. Tests and CI set this to a temporary directory so
#: they never write to the committed experiment.
ENVIRONMENT_VARIABLE = "SALTLENS_EXPERIMENTS_DIR"

#: The committed registry, relative to the repository root.
DEFAULT_RELATIVE_PATH = "experiments"


class ExperimentPaths:
    """The five directories and three files an experiment is made of.

    Resolved once at construction so a caller cannot be surprised by the registry
    moving between the moment it is located and the moment it is written.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else self._default_root()

    @staticmethod
    def _default_root() -> Path:
        override = os.environ.get(ENVIRONMENT_VARIABLE)
        if override:
            return Path(override)
        # parents[0]=app/experiments, [1]=app, [2]=backend, [3]=repo root.
        return Path(__file__).resolve().parents[3] / DEFAULT_RELATIVE_PATH

    # -- directories --------------------------------------------------------

    @property
    def events_dir(self) -> Path:
        return self.root / "events"

    @property
    def records_dir(self) -> Path:
        return self.events_dir / "records"

    @property
    def schema_dir(self) -> Path:
        return self.events_dir / "schema"

    @property
    def results_dir(self) -> Path:
        return self.root / "results"

    @property
    def operator_study_dir(self) -> Path:
        return self.root / "operator-study"

    @property
    def operator_study_records_dir(self) -> Path:
        return self.operator_study_dir / "records"

    # -- files --------------------------------------------------------------

    @property
    def manifest_file(self) -> Path:
        """The frozen version manifest: what produced these results."""
        return self.root / "VERSION.json"

    @property
    def events_index_file(self) -> Path:
        return self.events_dir / "index.json"

    @property
    def events_csv_file(self) -> Path:
        return self.events_dir / "events.csv"

    @property
    def operator_study_index_file(self) -> Path:
        return self.operator_study_dir / "index.json"

    def record_file(self, event_id: str) -> Path:
        return self.records_dir / f"{event_id}.json"

    def all_directories(self) -> tuple[Path, ...]:
        return (
            self.root,
            self.events_dir,
            self.records_dir,
            self.schema_dir,
            self.results_dir,
            self.operator_study_dir,
            self.operator_study_records_dir,
        )

    def ensure(self) -> ExperimentPaths:
        """Create every directory if it is missing.

        Called by the store on write rather than by the application at start-up:
        a read-only process should be able to read a committed experiment
        without first creating an empty results directory beside it.
        """
        for directory in self.all_directories():
            directory.mkdir(parents=True, exist_ok=True)
        return self


__all__ = ["DEFAULT_RELATIVE_PATH", "ENVIRONMENT_VARIABLE", "ExperimentPaths"]
