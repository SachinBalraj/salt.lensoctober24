from __future__ import annotations

from typing import Any, Generic, TypeVar

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.db.errors import (
    DatabaseUnavailableError,
    DocumentNotFoundError,
    DuplicateDocumentError,
)
from app.db.resolution import resolve_database
from app.models.base import BaseDocument

ModelT = TypeVar("ModelT", bound=BaseDocument)


class MongoRepository(Generic[ModelT]):
    """Read and write one collection, in terms of its model.

    ``database`` is either an already-created database or a zero-argument
    provider returning one; see :func:`app.db.resolution.resolve_database`. The
    resolution happens here, once, rather than in each service that builds a
    repository -- a service that forgot to would subscript the provider itself
    and fail with ``'function' object is not subscriptable`` on the first query,
    long after the omission.
    """

    def __init__(self, database: Any, collection_name: str, model: type[ModelT]) -> None:
        self._collection = resolve_database(database)[collection_name]
        self._collection_name = collection_name
        self._model = model

    async def insert(self, document: ModelT) -> ModelT:
        try:
            result = await self._collection.insert_one(document.to_mongo())
        except DuplicateKeyError as exc:
            raise DuplicateDocumentError(
                f"A document with the same natural key already exists in {self._collection_name}."
            ) from exc
        except Exception as exc:
            raise DatabaseUnavailableError(
                f"Could not write to MongoDB collection {self._collection_name}."
            ) from exc
        return document.model_copy(update={"id": result.inserted_id})

    async def get(self, field: str, value: Any) -> ModelT:
        try:
            document = await self._collection.find_one({field: value})
        except Exception as exc:
            raise DatabaseUnavailableError(
                f"Could not read from MongoDB collection {self._collection_name}."
            ) from exc
        if document is None:
            raise DocumentNotFoundError(
                f"No {self._collection_name} document matched {field}={value!r}."
            )
        return self._model.from_mongo(document)

    async def count(self, query: dict[str, Any] | None = None) -> int:
        try:
            return await self._collection.count_documents(query or {})
        except Exception as exc:
            raise DatabaseUnavailableError(
                f"Could not read from MongoDB collection {self._collection_name}."
            ) from exc

    async def distinct(self, field: str, query: dict[str, Any] | None = None) -> list[Any]:
        """Every distinct value of one field under a query, for ledger counts."""
        try:
            return await self._collection.distinct(field, query or {})
        except Exception as exc:
            raise DatabaseUnavailableError(
                f"Could not read from MongoDB collection {self._collection_name}."
            ) from exc

    async def find(
        self,
        query: dict[str, Any] | None = None,
        *,
        sort: list[tuple[str, int]] | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[ModelT]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        if skip < 0:
            raise ValueError("skip must not be negative")
        try:
            cursor = self._collection.find(query or {})
            if sort:
                cursor = cursor.sort(sort)
            documents = await cursor.skip(skip).limit(limit).to_list(length=limit)
        except Exception as exc:
            raise DatabaseUnavailableError(
                f"Could not read from MongoDB collection {self._collection_name}."
            ) from exc
        return [self._model.from_mongo(document) for document in documents]

    async def list_by_pan(
        self,
        pan_id: str,
        *,
        timestamp_field: str = "timestamp",
        limit: int = 100,
    ) -> list[ModelT]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        try:
            cursor = self._collection.find({"pan_id": pan_id.lower()}).sort(
                timestamp_field, -1
            ).limit(limit)
            documents = await cursor.to_list(length=limit)
        except Exception as exc:
            raise DatabaseUnavailableError(
                f"Could not read from MongoDB collection {self._collection_name}."
            ) from exc
        return [self._model.from_mongo(document) for document in documents]

    async def replace(
        self,
        field: str,
        value: Any,
        document: ModelT,
    ) -> ModelT:
        return await self.replace_where({field: value}, document)

    async def replace_where(self, query: dict[str, Any], document: ModelT) -> ModelT:
        """Replace the document matching a whole query, not one field.

        Some natural keys are compound -- one explanation per ``(prediction_id,
        kind)``, say -- and matching a single field would replace the wrong row: a
        global summary and a local explanation can share a ``prediction_id``
        without being interchangeable.
        """
        try:
            stored = await self._collection.replace_one(
                query,
                document.to_mongo(),
                upsert=False,
            )
        except DuplicateKeyError as exc:
            raise DuplicateDocumentError(
                f"A document with the same natural key already exists in {self._collection_name}."
            ) from exc
        except Exception as exc:
            raise DatabaseUnavailableError(
                f"Could not update MongoDB collection {self._collection_name}."
            ) from exc
        if stored.matched_count == 0:
            raise DocumentNotFoundError(
                f"No {self._collection_name} document matched {query!r}."
            )
        return document

    async def delete(self, field: str, value: Any) -> None:
        try:
            result = await self._collection.delete_one({field: value})
        except Exception as exc:
            raise DatabaseUnavailableError(
                f"Could not delete from MongoDB collection {self._collection_name}."
            ) from exc
        if result.deleted_count == 0:
            raise DocumentNotFoundError(
                f"No {self._collection_name} document matched {field}={value!r}."
            )

    async def update_one(
        self,
        field: str,
        value: Any,
        updates: dict[str, Any],
    ) -> ModelT:
        try:
            document = await self._collection.find_one_and_update(
                {field: value},
                {"$set": updates},
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError as exc:
            raise DuplicateDocumentError(
                f"A document with the same natural key already exists in {self._collection_name}."
            ) from exc
        except Exception as exc:
            raise DatabaseUnavailableError(
                f"Could not update MongoDB collection {self._collection_name}."
            ) from exc
        if document is None:
            raise DocumentNotFoundError(
                f"No {self._collection_name} document matched {field}={value!r}."
            )
        return self._model.from_mongo(document)
