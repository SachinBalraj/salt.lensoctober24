class PersistenceError(Exception):
    status_code = 503
    code = "persistence_error"

    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code


class DatabaseNotConfiguredError(PersistenceError):
    status_code = 503
    code = "database_not_configured"


class DatabaseUnavailableError(PersistenceError):
    status_code = 503
    code = "database_unavailable"


class DuplicateDocumentError(PersistenceError):
    status_code = 409
    code = "duplicate_document"


class DocumentNotFoundError(PersistenceError):
    status_code = 404
    code = "document_not_found"


class IndexProvisioningError(PersistenceError):
    status_code = 503
    code = "index_provisioning_failed"
