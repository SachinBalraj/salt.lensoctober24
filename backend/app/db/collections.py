"""Collection names, in one place.

These are the strings every module agrees on when it reads or writes a
collection, and they are defined here rather than in :mod:`app.db.indexes` for a
specific reason: the index plan has to import the persistence models, and the
dataset catalogue needs the collection names in order to document where each
feature came from. Leaving the names in the index module would make the models
depend on the module that depends on them.

Keeping them here means there is still exactly one definition of each name, and
the dependency graph runs downwards: collections -> indexes -> models.
"""

USERS = "users"
SALT_PANS = "salt_pans"
PAN_OBSERVATIONS = "pan_observations"
WEATHER_OBSERVATIONS = "weather_observations"
DIGITAL_TWIN_STATES = "digital_twin_states"
SIMULATION_RUNS = "simulation_runs"
ML_PREDICTIONS = "ml_predictions"
EXPLANATIONS = "explanations"
RECOMMENDATIONS = "recommendations"
#: Versioned operational decision rules. Append-only like Module 10's risk
#: definitions: a rule is written once and revised by writing a new version, so a
#: recommendation can cite the exact content that produced it.
DECISION_RULES = "decision_rules"
OPERATOR_DECISIONS = "operator_decisions"
ACTUAL_OUTCOMES = "actual_outcomes"
MODEL_VERSIONS = "model_versions"
RESEARCH_DATASETS = "research_datasets"
RISK_DEFINITIONS = "risk_definitions"
MODEL_EVALUATIONS = "model_evaluations"
#: Append-only deployment lifecycle. One document per status change, so a
#: deployment can be reconstructed rather than inferred from a current status.
MODEL_DEPLOYMENTS = "model_deployments"
#: Append-only research experiment ledger. One document per completed run, keyed
#: by a content hash over everything the run recorded; nothing is ever updated
#: or deleted, so a stored result survives whatever is recorded afterwards.
RESEARCH_EXPERIMENTS = "research_experiments"
#: Module 20 - user-reported rainfall events for field validation. One document
#: per reported event, with the pre/post snapshots, the actual rainfall and any
#: interventions embedded, so the report is reproducible from the record alone.
RAINFALL_EVENTS = "rainfall_events"

ALL_COLLECTIONS: tuple[str, ...] = (
    USERS,
    SALT_PANS,
    PAN_OBSERVATIONS,
    WEATHER_OBSERVATIONS,
    DIGITAL_TWIN_STATES,
    SIMULATION_RUNS,
    ML_PREDICTIONS,
    EXPLANATIONS,
    RECOMMENDATIONS,
    DECISION_RULES,
    OPERATOR_DECISIONS,
    ACTUAL_OUTCOMES,
    MODEL_VERSIONS,
    RESEARCH_DATASETS,
    RISK_DEFINITIONS,
    MODEL_EVALUATIONS,
    MODEL_DEPLOYMENTS,
    RESEARCH_EXPERIMENTS,
    RAINFALL_EVENTS,
)

__all__ = [
    "ACTUAL_OUTCOMES",
    "ALL_COLLECTIONS",
    "DECISION_RULES",
    "DIGITAL_TWIN_STATES",
    "EXPLANATIONS",
    "ML_PREDICTIONS",
    "MODEL_DEPLOYMENTS",
    "MODEL_EVALUATIONS",
    "MODEL_VERSIONS",
    "OPERATOR_DECISIONS",
    "PAN_OBSERVATIONS",
    "RAINFALL_EVENTS",
    "RECOMMENDATIONS",
    "RESEARCH_DATASETS",
    "RESEARCH_EXPERIMENTS",
    "RISK_DEFINITIONS",
    "SALT_PANS",
    "SIMULATION_RUNS",
    "USERS",
    "WEATHER_OBSERVATIONS",
]
