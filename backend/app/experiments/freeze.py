"""Phase 2 - freezing what produced a result.

An experimental result without the version it was computed under is a number
without a provenance, and a number without a provenance is an anecdote. This
module builds the record that closes that gap: git commit, application version,
physics model version, decision-rule identity, database schema version,
configuration, weather-data source, pan, and the instant the experiment ran.

Two properties make the manifest worth more than a printed version string.

It is **content-hashed**, and the hash is written onto every sealed prediction
(:class:`app.experiments.models.PredictionLock`). So if the physics, the rule or
the code changes between two events, that is detectable from the records
themselves rather than inferred from the fact that two runs happened.

And it is **read from the live system where a live system exists**. The decision
rule name, version and hash are read out of the database, not typed in, so a
manifest cannot quietly describe a rule that is not the one that fired. When the
database is unreachable that is reported as a gap rather than filled in with the
value that would be convenient.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models.base import normalize_datetime, utcnow

#: Bumped when the shape of the manifest changes incompatibly, so a reader can
#: tell an old record from a new one without guessing.
MANIFEST_VERSION = "1.0"

#: The collections whose contents the experiment's meaning depends on. If a
#: result is regenerated and these differ, the result may not be the same
#: experiment, and the manifest says so by hash rather than by memory.
FINGERPRINTED_COLLECTIONS: tuple[str, ...] = (
    "salt_pans",
    "pan_observations",
    "weather_observations",
    "rainfall_events",
    "simulation_runs",
    "recommendations",
    "operator_decisions",
    "decision_rules",
    "model_versions",
    "research_datasets",
)

#: Environment variables whose value changes what the system computes. Recorded
#: by name; values that are credentials are never recorded.
CONFIGURATION_KEYS: tuple[str, ...] = (
    "APP_ENV",
    "BACKEND_HOST",
    "BACKEND_PORT",
    "DATABASE_NAME",
    "ENABLE_MOCK_SIMULATIONS",
    "ML_MODEL_DIR",
    "RATE_LIMIT_ENABLED",
    "RATE_LIMIT_PER_MINUTE",
    "SENSOR_INGEST_ENABLED",
)


class ManifestFieldUnavailable(Exception):
    """A field the manifest must carry that could not be established.

    Available for a caller that wants a hard failure. The manifest builder itself
    does not raise it, because it records the gap in
    :attr:`FrozenManifest.unavailable` and returns a manifest anyway: a manifest
    that names what it could not establish is useful, and a manifest that fills
    the gap with a plausible value is worse than no manifest, because it will be
    believed. A caller that genuinely cannot proceed without the field raises
    this itself.
    """


@dataclass(frozen=True)
class FrozenManifest:
    """Everything needed to say which system produced a set of numbers."""

    manifest_version: str = MANIFEST_VERSION
    experiment_timestamp: datetime = field(default_factory=utcnow)

    git_commit: str | None = None
    git_commit_short: str | None = None
    git_dirty: bool | None = None

    app_name: str | None = None
    app_version: str | None = None

    simulation_model_name: str | None = None
    simulation_assumptions: tuple[str, ...] = ()
    twin_version: str | None = None

    rule_name: str | None = None
    rule_version: int | None = None
    rule_specification_hash: str | None = None

    schema_versions: dict[str, int] = field(default_factory=dict)
    collection_fingerprints: dict[str, str | None] = field(default_factory=dict)
    collection_counts: dict[str, int | None] = field(default_factory=dict)

    configuration: dict[str, str] = field(default_factory=dict)
    weather_data_source: str | None = None
    weather_provider_registry: tuple[str, ...] = ()
    pan_ids: tuple[str, ...] = ()

    #: Fields that could not be established, with the reason. Never empty-by-
    #: omission: an unavailable field is stated, so its absence is a finding
    #: rather than a silent gap.
    unavailable: dict[str, str] = field(default_factory=dict)

    def content_hash_payload(self) -> dict[str, Any]:
        """The subset that identifies this configuration of the system.

        Deliberately excludes :attr:`experiment_timestamp`, the collection
        fingerprints and the pan list. Those change every time the registry is
        re-read even when nothing about the physics or the rules has changed, so
        including them would make every re-read look like a different
        experiment. What is included is what would change a *prediction*.
        """
        payload = asdict(self)
        for volatile in (
            "experiment_timestamp",
            "collection_fingerprints",
            "collection_counts",
            "pan_ids",
            "unavailable",
            "manifest_version",
        ):
            payload.pop(volatile, None)
        return payload

    def content_hash(self) -> str:
        """A stable hash of :meth:`content_hash_payload`.

        Sorted keys, no whitespace, so the same configuration always produces
        the same string and a changed one always produces a different string.
        """
        encoded = json.dumps(
            self.content_hash_payload(), sort_keys=True, default=str, separators=(",", ":")
        )
        return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["experiment_timestamp"] = normalize_datetime(self.experiment_timestamp).isoformat()
        data["content_hash"] = self.content_hash()
        data["simulation_assumptions"] = list(self.simulation_assumptions)
        data["weather_provider_registry"] = list(self.weather_provider_registry)
        data["pan_ids"] = list(self.pan_ids)
        return data


def read_git_commit(repository_root: Path | str) -> tuple[str | None, bool | None]:
    """The current commit and whether the tree is dirty.

    Returns ``(None, None)`` where git is unavailable rather than a placeholder,
    so a manifest built outside a checkout says so instead of claiming a commit
    it does not have.
    """
    root = Path(repository_root)
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None, None
    if not commit:
        return None, None
    return commit, bool(status)


def read_environment_configuration() -> dict[str, str]:
    """The recorded subset of the environment.

    Names only from :data:`CONFIGURATION_KEYS`. No URI, no secret, no token: a
    manifest is committed and published, and a credential in one would be a
    disclosure rather than a reproducibility record.
    """
    recorded: dict[str, str] = {}
    for key in CONFIGURATION_KEYS:
        value = os.environ.get(key)
        if value is not None:
            recorded[key] = value
    return recorded


async def freeze_manifest(
    *,
    repository_root: Path | str | None = None,
    database: Any | None = None,
    pan_ids: list[str] | None = None,
    experiment_timestamp: datetime | None = None,
) -> FrozenManifest:
    """Build the manifest for one experiment run.

    Async because the database handle is Motor's, whose cursors are awaitable.
    A synchronous version of this would have to either block the event loop or
    accept a partly-read database, and both would make the manifest a record of
    something other than the state the experiment actually saw.

    ``database`` is optional. When absent, every database-derived field is
    recorded in :attr:`FrozenManifest.unavailable` with the reason, and the
    manifest is still returned — a manifest with stated gaps is usable, and
    refusing to build one would leave a perfectly reproducible offline run with
    nothing to say what produced it.
    """
    from app.config import get_settings
    from app.models.dataset import BUILDER_SCHEMA_VERSION
    from app.models.decision_rule import DecisionRule
    from app.models.digital_twin_state import DigitalTwinState
    from app.models.experiment import EXPERIMENT_MANIFEST_VERSION
    from app.models.risk_definition import RISK_DEFINITION_SCHEMA_VERSION
    from app.services.mass_balance import (
        MASS_BALANCE_MODEL_NAME,
        SIMULATION_ASSUMPTIONS,
    )

    # Only the models that carry a schema version are listed. Recording a version
    # for a model that has none would put a number in the manifest that the
    # system never writes anywhere, which is the same defect as an invented
    # measurement.
    schema_versions: dict[str, int | str] = {
        "decision_rule": DecisionRule.model_fields["schema_version"].default,
        "dataset_builder": BUILDER_SCHEMA_VERSION,
        "experiment_manifest": EXPERIMENT_MANIFEST_VERSION,
        "risk_definition": RISK_DEFINITION_SCHEMA_VERSION,
    }

    settings = get_settings()
    # parents[0]=app/experiments, [1]=app, [2]=backend, [3]=repo root.
    root = Path(repository_root) if repository_root else Path(__file__).resolve().parents[3]
    commit, dirty = read_git_commit(root)

    unavailable: dict[str, str] = {}

    # -- the rule that is actually in force ---------------------------------
    rule_name: str | None = None
    rule_version: int | None = None
    rule_hash: str | None = None
    fingerprints: dict[str, str | None] = {}
    counts: dict[str, int | None] = {}
    weather_source: str | None = None

    if database is None:
        unavailable["decision_rule"] = (
            "No database handle was supplied, so the rule in force could not be "
            "read. The manifest does not name one rather than naming the rule "
            "that is expected to be current."
        )
        unavailable["collection_fingerprints"] = (
            "No database handle was supplied, so no collection could be counted "
            "or fingerprinted."
        )
        unavailable["weather_data_source"] = (
            "No database handle was supplied, so the source of the newest "
            "weather record could not be read."
        )
    else:
        try:
            rule_name, rule_version, rule_hash = await _read_active_rule(database)
        except Exception as exc:  # noqa: BLE001 - a read failure is a gap, not a crash
            unavailable["decision_rule"] = (
                f"The decision_rules collection could not be read ({type(exc).__name__}). "
                "No rule is named rather than naming one that was not observed."
            )
        if rule_hash is None and "decision_rule" not in unavailable:
            unavailable["decision_rule"] = (
                "No active decision rule was found in the database, so none is "
                "named. Every recommendation this experiment grades would have "
                "had to cite one, so this indicates no rule has been registered."
            )
        try:
            fingerprints, counts = await _fingerprint_collections(database)
        except Exception as exc:  # noqa: BLE001
            unavailable["collection_fingerprints"] = (
                f"The collections could not be fingerprinted ({type(exc).__name__}). "
                "No fingerprint is recorded, so this manifest cannot show whether "
                "the corpus was the same as the one an earlier run saw."
            )
        try:
            weather_source = await read_weather_source(database)
        except Exception as exc:  # noqa: BLE001
            unavailable["weather_data_source"] = (
                f"The weather_observations collection could not be read "
                f"({type(exc).__name__})."
            )

    return FrozenManifest(
        experiment_timestamp=experiment_timestamp or utcnow(),
        git_commit=commit,
        git_commit_short=commit[:12] if commit else None,
        git_dirty=dirty,
        app_name=settings.app_name,
        app_version=settings.app_version,
        simulation_model_name=MASS_BALANCE_MODEL_NAME,
        simulation_assumptions=tuple(SIMULATION_ASSUMPTIONS),
        twin_version=DigitalTwinState.model_fields["twin_version"].default,
        rule_name=rule_name,
        rule_version=rule_version,
        rule_specification_hash=rule_hash,
        schema_versions=schema_versions,
        collection_fingerprints=fingerprints,
        collection_counts=counts,
        configuration=read_environment_configuration(),
        weather_data_source=weather_source,
        pan_ids=tuple(sorted(pan_ids or ())),
        unavailable=unavailable,
    )


async def _read_active_rule(database: Any) -> tuple[str | None, int | None, str | None]:
    """Name, version and hash of the active rule, read from the database.

    ``is_active`` is read first; a system with exactly one rule but none flagged
    active falls back to the highest version of the most recently created rule,
    and the caller is told via ``unavailable`` only when nothing at all was
    found. Guessing among several rules is not an option, so it never happens.
    """
    from app.db.collections import DECISION_RULES

    try:
        rows = await database[DECISION_RULES].find({}).to_list(length=None)
    except Exception:  # noqa: BLE001 - a read failure is a gap, not a crash
        return None, None, None

    documents = [row for row in (rows or []) if isinstance(row, dict)]
    if not documents:
        return None, None, None

    active = [row for row in documents if row.get("is_active")]
    if not active:
        active = [max(documents, key=lambda row: (row.get("version") or 0, str(row.get("created_at"))))]
    chosen = max(active, key=lambda row: (row.get("version") or 0, str(row.get("created_at"))))
    return (
        chosen.get("name"),
        chosen.get("version"),
        chosen.get("specification_hash"),
    )


async def _fingerprint_collections(database: Any) -> tuple[dict[str, str | None], dict[str, int | None]]:
    """Count and hash each collection, so a changed corpus is visible.

    The fingerprint is a hash of every document's ``_id`` in sorted order: it
    changes when a record is added, removed or re-identified, which is the point,
    and it does not require trusting the contents to be well formed.
    """
    fingerprints: dict[str, str | None] = {}
    counts: dict[str, int | None] = {}
    for name in FINGERPRINTED_COLLECTIONS:
        try:
            identifiers = await database[name].find({}).to_list(length=None)
        except Exception:  # noqa: BLE001
            fingerprints[name] = None
            counts[name] = None
            continue
        keys = sorted(str(row.get("_id")) for row in (identifiers or []))
        counts[name] = len(keys)
        joined = "|".join(keys).encode("utf-8")
        fingerprints[name] = f"sha256:{hashlib.sha256(joined).hexdigest()[:32]}"
    return fingerprints, counts


async def read_weather_source(database: Any) -> str | None:
    """The ``source`` of the newest weather record, or ``None``.

    A single string rather than a provider identity, because that is what the
    weather record itself claims. No weather provider is wired in this build, so
    in practice this reads ``manual`` or ``demonstration`` — which is the
    distinction the study depends on.
    """
    from app.db.collections import WEATHER_OBSERVATIONS

    try:
        rows = (
            await database[WEATHER_OBSERVATIONS]
            .find({})
            .sort("timestamp", -1)
            .limit(1)
            .to_list(length=1)
        )
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    newest = rows[0]
    source = newest.get("source")
    return str(source) if source is not None else None


__all__ = [
    "CONFIGURATION_KEYS",
    "FINGERPRINTED_COLLECTIONS",
    "MANIFEST_VERSION",
    "FrozenManifest",
    "ManifestFieldUnavailable",
    "freeze_manifest",
    "read_environment_configuration",
    "read_git_commit",
    "read_weather_source",
]
