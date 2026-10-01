"""In-memory stand-ins for the Motor collection API used by the repositories.

These let the request/response contract of a module be exercised end to end
without a live MongoDB. They implement only the query operators the
repositories actually use, and they raise ``DuplicateKeyError`` where a unique
index would in a real cluster.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


class FakeInsertResult:
    def __init__(self, inserted_id: Any) -> None:
        self.inserted_id = inserted_id


class FakeUpdateResult:
    def __init__(self, matched_count: int) -> None:
        self.matched_count = matched_count


class FakeDeleteResult:
    def __init__(self, deleted_count: int) -> None:
        self.deleted_count = deleted_count


class FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def sort(self, keys: list[tuple[str, int]]) -> FakeCursor:
        ordered = list(self._documents)
        for field, direction in reversed(keys):
            ordered.sort(key=lambda doc: doc.get(field), reverse=direction < 0)
        self._documents = ordered
        return self

    def skip(self, count: int) -> FakeCursor:
        self._documents = self._documents[count:]
        return self

    def limit(self, count: int) -> FakeCursor:
        self._documents = self._documents[:count]
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return self._documents[: length if length is not None else len(self._documents)]


class FakeCollection:
    """A single in-memory collection with optional unique keys.

    A unique key is either one field name or a tuple of them, for the composite
    keys the real cluster provisions. The distinction matters for anything that
    relies on a uniqueness violation: the version index is composite, so a fake
    that only understood single fields would never raise where Mongo does, and the
    retry path it guards would go untested.
    """

    def __init__(
        self,
        name: str,
        unique_fields: tuple[str | tuple[str, ...], ...] = (),
    ) -> None:
        self.name = name
        self._unique_fields = unique_fields
        self._documents: list[dict[str, Any]] = []

    def _violates_unique(self, document: dict[str, Any]) -> str | None:
        """The name of the unique key ``document`` would break, or None."""
        for key in self._unique_fields:
            fields = (key,) if isinstance(key, str) else key
            # A partial document (any component missing) is not a duplicate, which
            # is how Mongo treats a unique index on a sparse field too.
            if any(document.get(field) is None for field in fields):
                continue
            proposed = tuple(document.get(field) for field in fields)
            if any(
                all(existing.get(field) == value for field, value in zip(fields, proposed))
                for existing in self._documents
            ):
                return " + ".join(fields)
        return None

    # -- query matching ---------------------------------------------------

    @staticmethod
    def _resolve_path(document: dict[str, Any], path: str) -> Any:
        """Read a possibly dotted field path out of a document.

        Mongo matches ``"a.b"`` against a nested document, and a query that uses
        the notation is not an error there. Without this a dotted query would
        silently read ``None`` and match nothing, which is the worst possible
        failure for a stand-in: the test passes against a wrong answer.
        """
        value: Any = document
        for part in path.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value

    @classmethod
    def _matches(cls, document: dict[str, Any], query: dict[str, Any]) -> bool:
        for field, condition in query.items():
            if field == "$or":
                if not any(cls._matches(document, clause) for clause in condition):
                    return False
                continue
            value = cls._resolve_path(document, field)
            if isinstance(condition, dict):
                if not cls._matches_operators(value, condition):
                    return False
                continue
            if value != condition:
                return False
        return True

    @staticmethod
    def _matches_operators(value: Any, condition: dict[str, Any]) -> bool:
        for operator, expected in condition.items():
            if operator == "$ne" and value == expected:
                return False
            if operator == "$gte" and not (value is not None and value >= expected):
                return False
            if operator == "$gt" and not (value is not None and value > expected):
                return False
            if operator == "$lte" and not (value is not None and value <= expected):
                return False
            if operator == "$lt" and not (value is not None and value < expected):
                return False
            if operator == "$in" and value not in expected:
                return False
            if operator == "$regex":
                flags = re.IGNORECASE if "i" in condition.get("$options", "") else 0
                if not isinstance(value, str) or not re.search(expected, value, flags):
                    return False
        return True

    # -- Motor API --------------------------------------------------------

    async def insert_one(self, document: dict[str, Any]) -> FakeInsertResult:
        violation = self._violates_unique(document)
        if violation is not None:
            raise DuplicateKeyError(f"{violation} must be unique in {self.name}")
        stored = {**document, "_id": ObjectId()}
        self._documents.append(stored)
        return FakeInsertResult(stored["_id"])

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for document in self._documents:
            if self._matches(document, query):
                return dict(document)
        return None

    def find(self, query: dict[str, Any]) -> FakeCursor:
        return FakeCursor(
            [dict(document) for document in self._documents if self._matches(document, query)]
        )

    async def count_documents(self, query: dict[str, Any]) -> int:
        return sum(1 for document in self._documents if self._matches(document, query))

    async def distinct(self, field: str, query: dict[str, Any]) -> list[Any]:
        seen: set[Any] = set()
        for document in self._documents:
            if query and not self._matches(document, query):
                continue
            seen.add(self._resolve_path(document, field))
        return list(seen)

    async def replace_one(
        self, query: dict[str, Any], replacement: dict[str, Any], upsert: bool = False
    ) -> FakeUpdateResult:
        for index, document in enumerate(self._documents):
            if self._matches(document, query):
                preserved_id = document.get("_id")
                self._documents[index] = {**replacement, "_id": preserved_id}
                return FakeUpdateResult(1)
        return FakeUpdateResult(0)

    async def find_one_and_update(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        return_document: int = ReturnDocument.AFTER,
    ) -> dict[str, Any] | None:
        for index, document in enumerate(self._documents):
            if self._matches(document, query):
                self._documents[index] = {**document, **update.get("$set", {})}
                return dict(self._documents[index])
        return None

    async def delete_one(self, query: dict[str, Any]) -> FakeDeleteResult:
        for index, document in enumerate(self._documents):
            if self._matches(document, query):
                del self._documents[index]
                return FakeDeleteResult(1)
        return FakeDeleteResult(0)

    def documents(self) -> list[dict[str, Any]]:
        """Direct read access for assertions about what was actually stored."""
        return [dict(document) for document in self._documents]

    def amend(self, field: str, value: Any, **changes: Any) -> dict[str, Any]:
        """Change fields of one stored document, the way a prior write would have.

        For the cases a test cannot produce through the API: a stored report whose
        reading is absent, say, which the pipeline only yields for a dataset this
        suite does not build. Editing the stored document directly is honest about
        what is being exercised -- the reader, not the writer -- and beats building
        a fixture by hand that no real run would create.
        """
        for document in self._documents:
            if document.get(field) == value:
                document.update(changes)
                return dict(document)
        raise KeyError(f"no {self.name} document with {field}={value!r}")


class FakeDatabase:
    """A database whose collections are created on first access."""

    #: Mirrors the unique indexes the real cluster provisions.
    UNIQUE_FIELDS: ClassVar[dict[str, tuple[str | tuple[str, ...], ...]]] = {
        "salt_pans": ("pan_id",),
        "research_datasets": ("content_hash",),
        "risk_definitions": ("specification_hash", ("name", "version")),
        # Mirrors the unique (target, version) index the real cluster provisions.
        # A fake that did not enforce it would let a test register two versions
        # of one model where Mongo would reject the second write.
        "model_versions": (("target", "version"),),
        # Mirrors both unique indexes on evaluations. The (model_id, version) one is
        # what makes a re-run a replace rather than a second report, so a fake that
        # did not enforce it would let Module 9's replacement path go untested --
        # the run would succeed, insert a duplicate, and every assertion about
        # "one report per model version" would pass against the wrong behaviour.
        "model_evaluations": ("evaluation_id", ("model_id", "version")),
        # Mirrors the unique partial index on explanations: one local explanation
        # per prediction, enforced by (prediction_id). The index is partial on
        # kind="local", and a global summary carries prediction_id=None, so the
        # missing-component rule below exempts it exactly as the cluster's partial
        # filter does -- the two are the same rule, arrived at separately.
        #
        # Without this the fake would accept a second local row for one prediction,
        # the service's replace path would never be reached, and every assertion
        # about "one explanation per prediction" would pass against a collection
        # holding two answers to a question already answered.
        "explanations": ("explanation_id", ("prediction_id",)),
        # Mirrors both unique indexes on decision rules. The content hash is what
        # makes a re-post of an unchanged rule return the existing document rather
        # than write a second one, and the (name, version) pair is what makes the
        # version allocation's retry path reachable. Without these, Module 12's
        # "one rule per hash" and "one version per name" guarantees would be
        # untested against a fake that never raised.
        "decision_rules": ("specification_hash", ("name", "version")),
        # Mirrors the unique content-hash index on the experiment ledger: a
        # re-post of an identical record returns the existing document, and a
        # fake that did not enforce it would let a second identical insert
        # succeed and every "never overwrite" assertion pass against the wrong
        # behaviour.
        "research_experiments": ("content_hash",),
        # Mirrors the unique index on recommendations per operator decision. The
        # service checks for an existing decision before it writes, so without
        # this the fake would accept the pair a concurrent write produces and the
        # branch that turns a DuplicateDocumentError into the same
        # `decision_already_recorded` refusal would never run — every
        # "one decision per recommendation" assertion would pass against a
        # collection holding two dispositions of one advice.
        "operator_decisions": ("recommendation_id",),
    }

    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(
            name, FakeCollection(name, self.UNIQUE_FIELDS.get(name, ()))
        )
