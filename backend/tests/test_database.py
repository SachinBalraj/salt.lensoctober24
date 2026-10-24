from __future__ import annotations

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.config import settings
from app.db.errors import (
    DatabaseUnavailableError,
    DocumentNotFoundError,
    DuplicateDocumentError,
)
from app.db.indexes import INDEX_PLAN, registry_collections
from app.db.mongo import MongoDatabase, db
from app.db.repository import MongoRepository
from app.db.resolution import resolve_database
from app.models import SaltPan

#: A syntactically valid Atlas URI pointing at a host that does not resolve. It is
#: used only where nothing may connect, so a test that reached it would fail
#: loudly rather than quietly touching a real cluster.
UNREACHABLE_URI = "mongodb+srv://user:password@cluster.invalid/saltlens"


class FakeAdmin:
    def __init__(self, error=None):
        self.error = error
        self.commands = 0

    async def command(self, name):
        self.commands += 1
        if self.error:
            raise self.error


class FakeCollection:
    def __init__(self):
        self.indexes = []

    async def create_index(self, keys, **kwargs):
        self.indexes.append((keys, kwargs))


class FakeDatabase:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


class FakeClient:
    def __init__(self, error=None):
        self.admin = FakeAdmin(error)
        self.database = FakeDatabase()
        self.closed = False

    def __getitem__(self, name):
        return self.database

    def close(self):
        self.closed = True


def pan() -> SaltPan:
    return SaltPan(
        pan_id="pan-1",
        pan_name="Pan",
        location_name="Location",
        area_m2=100,
        normal_depth_cm=10,
        target_harvest_salinity_g_l=250,
        risk_thresholds={"flood_depth_cm": 20, "target_flow_cm": 5},
    )


@pytest.mark.asyncio
async def test_unconfigured_database_is_explicitly_not_configured():
    database = MongoDatabase(uri=None)
    assert await database.connect() == "not_configured"
    assert (await database.check_health())["status"] == "not_configured"
    assert database.indexes_ready is False


def test_an_explicit_none_uri_ignores_the_uri_the_process_can_see(monkeypatch):
    """`uri=None` means "no database", not "look one up in the environment".

    A developer's ``backend/.env`` usually holds a live Atlas URI, so a database
    built from the default sentinel is configured while one built from an
    explicit ``None`` is not. Reading the environment for the explicit ``None``
    would make every test that means to simulate an absent database silently
    operate on the real cluster instead.
    """
    monkeypatch.setattr(settings, "mongodb_uri", UNREACHABLE_URI)

    assert MongoDatabase().configured is True
    assert MongoDatabase(uri=None).configured is False


def test_the_suite_never_reaches_the_cluster_however_it_is_configured(monkeypatch):
    """The shared manager stays unconfigured even with a URI available.

    ``tests/conftest.py`` asks for this before the application is imported. It is
    asserted here against a URI the process demonstrably can see, so the suite
    cannot read or write real records by accident if the flag were ever dropped.
    """
    monkeypatch.setattr(settings, "mongodb_uri", UNREACHABLE_URI)

    assert db.configured is False
    assert "MONGODB_URI" in (db.unavailable_reason() or "")
    with pytest.raises(HTTPException) as error:
        db.get_db()
    assert error.value.status_code == 503


@pytest.fixture()
def offline_motor_database():
    """A real Motor database that has never contacted a server.

    ``connect=False`` makes the driver open no connection, so this exercises the
    genuine Motor type -- the one whose ``__call__`` raises
    ``MotorDatabase object is not callable`` -- without reaching a cluster.
    """
    client = AsyncIOMotorClient(UNREACHABLE_URI, connect=False, serverSelectionTimeoutMS=100)
    yield client["saltlens"]
    client.close()


def test_a_database_is_returned_rather_than_called(offline_motor_database):
    """A Motor database is subscriptable, so only its type stops it being called."""
    assert callable(offline_motor_database)
    assert resolve_database(offline_motor_database) is offline_motor_database


def test_a_provider_is_called_to_reach_the_database(offline_motor_database):
    assert resolve_database(lambda: offline_motor_database) is offline_motor_database


def test_credentialed_mongo_uri_is_still_configured():
    database = MongoDatabase(
        uri="mongodb://username:password@cluster.example.test/saltlens"
    )
    assert database.configured is True


@pytest.mark.asyncio
async def test_connection_health_pings_and_provisions_indexes_once():
    client = FakeClient()
    database = MongoDatabase(
        uri="mongodb://localhost:27017",
        database_name="saltlens_test",
        client_factory=lambda *args, **kwargs: client,
    )
    assert await database.connect() == "connected"
    first_calls = client.admin.commands
    assert first_calls == 1
    assert database.indexes_ready is True
    assert await database.check_health() == await database.check_health()
    assert client.admin.commands == first_calls + 2
    # Every registered collection, rather than a literal: a new collection that
    # reached the registry without reaching the index plan should fail here.
    assert set(client.database.collections) == set(registry_collections())
    await database.close()
    assert client.closed is True


@pytest.mark.asyncio
async def test_an_index_provisioning_failure_is_not_reported_as_a_connection_failure():
    """A ping that succeeds must not be followed by an "unreachable" verdict.

    The health endpoint's error field used to reuse the connection-failure
    sentence for anything ``ensure_indexes`` raised, so a cluster whose user
    could read but not create indexes reported ``connected=True`` *and*
    ``MongoDB is unreachable.`` in the same body. The two cannot both be true.
    """
    class IndexFailDatabase(FakeDatabase):
        def __getitem__(self, name):
            collection = self.collections.get(name)
            if collection is None:
                collection = IndexFailCollection()
                self.collections[name] = collection
            return collection

    class IndexFailCollection(FakeCollection):
        async def create_index(self, keys, **kwargs):
            raise PyMongoError("not authorized on saltlens to execute command createIndexes")

    client = FakeClient()
    client.database = IndexFailDatabase()
    database = MongoDatabase(
        uri="mongodb://localhost:27017",
        database_name="saltlens_test",
        client_factory=lambda *args, **kwargs: client,
    )
    health = await database.check_health()
    assert health["status"] == "connected"
    assert health["connected"] is True
    assert health["indexes_ready"] is False
    assert "unreachable" not in (health["error"] or "")
    assert "indexes" in (health["error"] or "")
    # A later successful run clears the stale error, not preserves it.
    assert await database.check_health() == health
    await database.close()


@pytest.mark.asyncio
async def test_a_recovered_connection_reprovisions_indexes_and_clears_the_error():
    """Recovery after a dropped ping must not leave ``indexes_ready`` stuck false.

    The connection flag and the index flag are written by the same health pass,
    so a ping that fails after provisioning used to clear ``indexes_ready``
    without clearing the attempt flag. The next successful ping then saw the
    attempt as already made, skipped provisioning and reported ``connected=True``
    beside a stale ``MongoDB is unreachable.`` -- the same contradiction the
    provisioning test above guards against, reached from the connection side.
    """

    class FlakyAdmin(FakeAdmin):
        def __init__(self):
            super().__init__()
            self.fail = False

        async def command(self, name):
            self.commands += 1
            if self.fail:
                raise PyMongoError("ping failed")

    client = FakeClient()
    client.admin = FlakyAdmin()
    database = MongoDatabase(
        uri="mongodb://localhost:27017",
        database_name="saltlens_test",
        client_factory=lambda *args, **kwargs: client,
    )
    assert await database.connect() == "connected"
    assert database.indexes_ready is True

    client.admin.fail = True
    down = await database.check_health()
    assert down["status"] == "error"
    assert down["connected"] is False
    assert database.indexes_ready is False

    client.admin.fail = False
    recovered = await database.check_health()
    assert recovered["status"] == "connected"
    assert recovered["connected"] is True
    assert recovered["indexes_ready"] is True
    assert not recovered.get("error")
    await database.close()


@pytest.mark.asyncio
async def test_failed_ping_keeps_get_db_unavailable():
    client = FakeClient(error=PyMongoError("ping failed"))
    database = MongoDatabase(
        uri="mongodb://localhost:27017",
        database_name="saltlens_test",
        client_factory=lambda *args, **kwargs: client,
    )
    assert await database.connect() == "error"
    assert (await database.check_health())["status"] == "error"
    with pytest.raises(Exception) as error:
        database.get_db()
    assert getattr(error.value, "status_code", None) == 503
    await database.close()


class RepositoryCollection:
    def __init__(self, document=None, duplicate=False):
        self.document = document
        self.duplicate = duplicate

    async def insert_one(self, document):
        if self.duplicate:
            raise DuplicateKeyError("duplicate")
        self.document = dict(document)
        self.document["_id"] = "inserted"
        return type("InsertResult", (), {"inserted_id": "inserted"})()

    async def find_one(self, query):
        return self.document

    async def delete_one(self, query):
        if self.document is None:
            return type("DeleteResult", (), {"deleted_count": 0})()
        self.document = None
        return type("DeleteResult", (), {"deleted_count": 1})()


class RepositoryDatabase:
    def __init__(self, collection):
        self.collection = collection

    def __getitem__(self, name):
        return self.collection


@pytest.mark.asyncio
async def test_repository_translates_duplicate_and_not_found_errors():
    collection = RepositoryCollection(duplicate=True)
    repository = MongoRepository(RepositoryDatabase(collection), "salt_pans", SaltPan)
    with pytest.raises(DuplicateDocumentError):
        await repository.insert(pan())

    empty_repository = MongoRepository(
        RepositoryDatabase(RepositoryCollection()), "salt_pans", SaltPan
    )
    with pytest.raises(DocumentNotFoundError):
        await empty_repository.get("pan_id", "missing")


@pytest.mark.asyncio
async def test_repository_maps_database_failures():
    class BrokenCollection(RepositoryCollection):
        async def find_one(self, query):
            raise RuntimeError("network")

    repository = MongoRepository(RepositoryDatabase(BrokenCollection()), "salt_pans", SaltPan)
    with pytest.raises(DatabaseUnavailableError):
        await repository.get("pan_id", "pan-1")


@pytest.mark.asyncio
async def test_a_repository_accepts_a_provider_as_well_as_a_database():
    """Routers hand services ``get_db`` itself, not what it returns.

    Subscripting that provider would raise ``'function' object is not
    subscriptable`` on the first query -- a 500 where the caller should have been
    told the database was unavailable.
    """
    collection = RepositoryCollection()
    repository = MongoRepository(
        lambda: RepositoryDatabase(collection), "salt_pans", SaltPan
    )

    await repository.insert(pan())
    assert collection.document["pan_id"] == "pan-1"


@pytest.mark.asyncio
async def test_the_manager_provisions_the_index_plan_and_repeating_it_is_harmless():
    """Index initialisation runs through the manager, against the real plan.

    Every spec carries a name, so provisioning twice creates nothing new: the
    second call is a no-op against an already-provisioned cluster rather than a
    second set of indexes.
    """
    client = FakeClient()
    database = MongoDatabase(
        uri="mongodb://localhost:27017",
        database_name="saltlens_test",
        client_factory=lambda *args, **kwargs: client,
    )
    assert await database.connect() == "connected"

    # Connecting provisions the plan, so the manager is already the path the
    # application uses rather than one some caller has to remember to take.
    first = await database.ensure_indexes()

    def index_names() -> dict[str, set[str]]:
        return {
            name: {kwargs["name"] for _keys, kwargs in collection.indexes}
            for name, collection in client.database.collections.items()
        }

    provisioned = index_names()
    second = await database.ensure_indexes()

    assert first.ready is True
    assert second.ready is True
    assert list(second) == list(first)
    assert set(provisioned) == set(registry_collections())
    assert provisioned == {
        spec.collection: {s.name for s in INDEX_PLAN if s.collection == spec.collection}
        for spec in INDEX_PLAN
    }
    # Re-asserting changed no collection's set of index names: create_index is
    # keyed by name, so a second pass cannot add a second index to any collection.
    assert index_names() == provisioned
    await database.close()


@pytest.mark.asyncio
async def test_an_unconnected_manager_refuses_to_provision_indexes():
    """There is no database to provision before one exists; it does not pretend."""
    database = MongoDatabase(uri="mongodb://localhost:27017")
    with pytest.raises(Exception) as error:
        await database.ensure_indexes()
    assert error.type.__name__ == "DatabaseNotConfiguredError"
    assert error.value.status_code == 503
