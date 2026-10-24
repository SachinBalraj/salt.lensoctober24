import pytest
from pymongo.errors import PyMongoError

from app.db import indexes as ix
from app.db.errors import IndexProvisioningError

EXPECTED_COLLECTIONS = {
    "users",
    "salt_pans",
    "pan_observations",
    "weather_observations",
    "digital_twin_states",
    "simulation_runs",
    "ml_predictions",
    "explanations",
    "recommendations",
    "decision_rules",
    "operator_decisions",
    "actual_outcomes",
    "model_versions",
    "model_evaluations",
    "model_deployments",
    "research_datasets",
    "risk_definitions",
    "research_experiments",
    "rainfall_events",
    "recommendation_deliveries",
}


def test_registry_covers_every_collection():
    # The count is asserted alongside the names so that adding a collection without
    # registering its model, or registering one without provisioning an index, fails
    # here rather than at the first query in production. The name is the message; the
    # number is the thing a reader scans.
    assert set(ix.registry_collections()) == EXPECTED_COLLECTIONS
    assert len(ix.COLLECTION_MODELS) == len(EXPECTED_COLLECTIONS)


def test_index_plan_has_no_duplicate_keys():
    pairs = [(spec.collection, spec.keys) for spec in ix.INDEX_PLAN]
    assert len(pairs) == len(set(pairs))
    assert all(spec.name for spec in ix.INDEX_PLAN)


def test_index_fields_exist_on_their_models():
    ix.validate_index_plan()
    for spec in ix.INDEX_PLAN:
        model = ix.COLLECTION_MODELS[spec.collection]
        assert all(
            ix.resolves_to_field(model, field) for field, _ in spec.keys
        ), spec.name


def test_a_dotted_index_field_resolves_through_a_nested_model():
    """Mongo indexes a sub-document as parent.child, and the plan does too."""
    target = ix.COLLECTION_MODELS["research_datasets"]

    assert ix.resolves_to_field(target, "target.name")
    assert ix.resolves_to_field(target, "content_hash")
    assert not ix.resolves_to_field(target, "target.not_a_field")
    assert not ix.resolves_to_field(target, "nope.name")


def test_pan_time_series_indexes_use_real_pan_and_time_fields():
    required = {
        "pan_observations": "timestamp",
        "weather_observations": "timestamp",
        "digital_twin_states": "as_of",
        "simulation_runs": "created_at",
        "ml_predictions": "timestamp",
        "recommendations": "issued_at",
        "operator_decisions": "timestamp",
        "actual_outcomes": "timestamp",
    }
    for collection, time_field in required.items():
        matching = [
            spec
            for spec in ix.INDEX_PLAN
            if spec.collection == collection
            and dict(spec.keys).get("pan_id") == 1
            and dict(spec.keys).get(time_field) == -1
        ]
        assert matching, collection


def test_unique_indexes_are_limited_to_natural_keys():
    unique = {
        (spec.collection, spec.keys)
        for spec in ix.INDEX_PLAN
        if spec.unique
    }
    assert ("users", (("username", 1),)) in unique
    assert ("salt_pans", (("pan_id", 1),)) in unique
    assert ("model_versions", (("target", 1), ("version", 1))) in unique
    assert all(
        not (spec.collection == "pan_observations" and spec.unique)
        for spec in ix.INDEX_PLAN
    )


#: Keys that a deployed cluster already holds as a *plain* index, and that have
#: since been promoted to unique. Provisioning will not change an index in place,
#: so a promoted key that keeps the name it was generated under fails every
#: startup with IndexKeySpecsConflict and never gets the constraint at all. A new
#: promotion is added here so the rename is a deliberate step rather than a
#: production incident.
PROMOTED_TO_UNIQUE = {("operator_decisions", (("recommendation_id", 1),))}


def test_a_promoted_key_is_renamed_so_it_can_be_created_alongside_its_plain_index():
    """A unique index may not reuse the name generated from its key.

    The cluster this was found on held ``operator_decisions_recommendation_id_asc``
    as a plain index from before the key became a natural key. Re-issuing that
    name with ``unique=True`` is not a no-op Mongo tolerates: it is refused, so
    the one-decision-per-recommendation guarantee was enforced by nothing but the
    service's pre-check, which is exactly what it cannot survive under concurrency.
    """
    for collection, keys in PROMOTED_TO_UNIQUE:
        matching = [s for s in ix.INDEX_PLAN if s.collection == collection and s.keys == keys]
        assert len(matching) == 1, f"{collection}{keys} should be declared exactly once"
        spec = matching[0]
        assert spec.unique, f"{collection}{keys} is listed as promoted but is not unique"
        plain = ix.IndexSpec(collection, keys)
        assert spec.name != plain.name, (
            f"{collection}{keys} keeps the name an earlier deployment created as a "
            "plain index, so provisioning cannot add the constraint"
        )


def test_one_decision_per_recommendation_is_unique_in_the_plan():
    """The guarantee the service checks for is also the one the cluster enforces."""
    specs = [
        spec
        for spec in ix.INDEX_PLAN
        if spec.collection == "operator_decisions"
        and spec.keys == (("recommendation_id", 1),)
    ]
    assert [spec.unique for spec in specs] == [True]
    # And only that one, so a fresh deployment does not carry a redundant plain
    # index on the key the unique one already serves.
    assert len(specs) == 1


class FakeCollection:
    def __init__(self, failures=()):
        self.calls = []
        self.failures = set(failures)

    async def create_index(self, keys, **kwargs):
        name = kwargs["name"]
        self.calls.append((keys, kwargs))
        if name in self.failures:
            raise PyMongoError(name)


class FakeDatabase:
    def __init__(self, failures=()):
        self.failures = failures
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection(self.failures))


@pytest.mark.asyncio
async def test_ensure_indexes_reports_each_created_index():
    database = FakeDatabase()
    report = await ix.ensure_indexes(database)
    assert report.ready is True
    assert len(report.created) == len(ix.INDEX_PLAN)
    assert sum(len(collection.calls) for collection in database.collections.values()) == len(
        ix.INDEX_PLAN
    )


@pytest.mark.asyncio
async def test_ensure_indexes_can_fail_strictly():
    failing_name = next(
        spec.name for spec in ix.INDEX_PLAN if spec.collection == "salt_pans"
    )
    database = FakeDatabase(failures={failing_name})
    report = await ix.ensure_indexes(database)
    assert report.ready is False
    with pytest.raises(IndexProvisioningError):
        await ix.ensure_indexes(database, strict=True)


@pytest.mark.asyncio
async def test_ensure_indexes_accepts_a_provider_as_well_as_a_database():
    """The routers pass ``get_db`` itself, so the plan must accept a provider.

    Index provisioning is the one caller that reaches past every service into the
    raw database; treating a provider as a database here would leave the cluster
    unindexed while every request reported success.
    """
    database = FakeDatabase()
    report = await ix.ensure_indexes(lambda: database)

    assert report.ready is True
    assert len(report.created) == len(ix.INDEX_PLAN)
    assert sum(
        len(collection.calls) for collection in database.collections.values()
    ) == len(ix.INDEX_PLAN)


@pytest.mark.asyncio
async def test_ensure_indexes_is_idempotent():
    """A second provisioning pass asserts the same named indexes, nothing more."""
    database = FakeDatabase()
    first = await ix.ensure_indexes(database)
    second = await ix.ensure_indexes(database)

    assert second.ready is True
    assert list(second) == list(first)
    assert all(
        {call[1]["name"] for call in collection.calls}
        == {spec.name for spec in ix.INDEX_PLAN if spec.collection == name}
        for name, collection in database.collections.items()
    )
