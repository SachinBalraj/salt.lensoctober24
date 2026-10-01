"""Containment for model-artifact paths.

Trained artifacts live under one configured directory (``ml/models``) and are
written there by the training pipeline. Every loader — prediction, evaluation,
explanation — joins what the registry records onto that directory and then
picks it apart with ``joblib``, which deserialises arbitrary Python. The path is
already server-controlled (it is written at training time, never accepted from a
request), but containment is cheap: one resolver that refuses anything that
escapes the directory, so a registry row corrupted by a compromise cannot turn
an artifact lookup into ``joblib.load`` of an arbitrary file.
"""

from __future__ import annotations

from pathlib import Path


class ArtifactPathError(ValueError):
    """The recorded artifact path resolves outside the artifact directory."""


def resolve_artifact_path(artifact_directory: Path, artifact_path: str) -> Path:
    """Resolve ``artifact_path`` inside ``artifact_directory``, or refuse.

    Absolute paths and ``..`` escapes both resolve to a location outside the
    directory, which is what is refused — the identity of the path does not
    matter, only where it lands.
    """
    base = Path(artifact_directory).resolve()
    candidate = (base / artifact_path).resolve()
    if candidate != base and base not in candidate.parents:
        raise ArtifactPathError(
            f"artifact path {artifact_path!r} resolves outside the artifact "
            f"directory {base}."
        )
    return candidate


__all__ = ["ArtifactPathError", "resolve_artifact_path"]