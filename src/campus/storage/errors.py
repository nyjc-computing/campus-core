"""campus.storage.errors

Storage-specific error definitions for Campus storage backends.

These errors are used by storage backends (documents and tables) to handle
common error conditions like documents not found or no changes applied.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# A credential-bearing URI fragment in driver text — pymongo's
# InvalidURI echoes the malformed connection string verbatim. Masked
# before the text reaches even the server log (policy §3: logs carry
# no secrets).
_CREDENTIAL_IN_URI = re.compile(r"(://[^:/@\s]+:)[^@\s]+(@)")


def sanitize_driver_error(
        err: Exception,
        *,
        operation: str,
        group_name: Optional[str] = None,
        error_cls: Optional["type[StorageError]"] = None,
) -> "StorageError":
    """Map a raw storage-driver exception to a sanitized StorageError.

    Driver exception texts embed internals: pymongo's ``InvalidURI``
    echoes the credential-bearing connection URI, psycopg2
    ``IntegrityError`` carries the failing SQL statement, boto
    ``ClientError`` carries endpoints and request ids. StorageError
    folds ``details`` into its message, so raw driver text would reach
    server logs and — through the generic error handler —
    student-visible responses (campus-core#8; environment-access-policy
    §3/§4).

    The returned error carries the driver class name, the operation,
    and the collection/table only. The full driver text goes to the
    server log (superadmin-only surface), with credential-bearing URI
    fragments masked.
    """
    logger.error(
        "Storage driver %s during %s on %s: %s",
        type(err).__name__,
        operation,
        group_name or "<unnamed>",
        _CREDENTIAL_IN_URI.sub(r"\1***\2", str(err)),
    )
    if error_cls is None:
        error_cls = StorageError
    return error_cls(
        f"{type(err).__name__} during {operation}",
        group_name=group_name,
    )


class StorageError(Exception):
    """Base class for all storage-related errors."""

    def __init__(
            self,
            message: str = "An error occurred in storage.",
            group_name: Optional[str] = None,
            details: Optional[dict] = None
    ):
        full_message = message
        if group_name:
            full_message += f" in group '{group_name}'"
            self.group_name = group_name
        if details:
            self.details = details
            full_message += f". Details: {details}"
        super().__init__(full_message)
        self.message = message


class ConflictError(StorageError):
    """Error raised when a storage operation encounters a conflict.

    This error is raised when an operation cannot proceed due to a conflict,
    such as attempting to insert a duplicate record or violating a unique constraint.
    """

    def __init__(
            self,
            message: str = "A conflict occurred in storage.",
            group_name: Optional[str] = None,
            details: Optional[dict] = None
    ):
        full_message = message
        if group_name:
            full_message += f" in group '{group_name}'"
            self.group_name = group_name
        if details:
            self.details = details
            full_message += f". Details: {details}"
        super().__init__(full_message)


class NoChangesAppliedError(StorageError):
    """Error raised when a storage operation affects no documents.

    This error is raised when attempting to update or delete documents
    with a query that matches no documents in the storage backend.
    """

    def __init__(
            self,
            operation: str,
            query: Optional[dict] = None,
            group_name: Optional[str] = None
    ):
        self.operation = operation
        message = f"No documents were affected by {operation} operation"
        if group_name:
            message += f" in group '{group_name}'"
            self.group_name = group_name
        if query:
            self.query = query
            message += f" with query: {query}"
        super().__init__(message)


class NotFoundError(StorageError):
    """Error raised when a document/row is not found in storage.

    This error is raised when attempting to update or delete a
    document/row that does not exist in the storage backend.
    """

    def __init__(self, doc_id: str, name: Optional[str] = None):
        self.doc_id = doc_id
        message = f"Document/row with id '{doc_id}' not found"
        if name:
            message += f" in collection/table '{name}'"
            self.group_name = name
        super().__init__(message)
