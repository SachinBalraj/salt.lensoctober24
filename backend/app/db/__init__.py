from app.db.errors import (
    DatabaseNotConfiguredError,
    DatabaseUnavailableError,
    DocumentNotFoundError,
    DuplicateDocumentError,
    IndexProvisioningError,
    PersistenceError,
)
from app.db.indexes import ensure_indexes
from app.db.mongo import MongoDatabase, db
from app.db.resolution import resolve_database

__all__ = [
    "DatabaseNotConfiguredError",
    "DatabaseUnavailableError",
    "DocumentNotFoundError",
    "DuplicateDocumentError",
    "IndexProvisioningError",
    "MongoDatabase",
    "PersistenceError",
    "db",
    "ensure_indexes",
    "resolve_database",
]
