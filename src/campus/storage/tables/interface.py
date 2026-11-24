"""campus.storage.tables.interface

This module provides the Tables storage interface.

Each row in the table is assumed to have:
1. An `id` primary key.
2. A `created_at` timestamp.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any

from campus.model import InternalModel, Model
from campus.storage.query import Agg, TimeBucket

# This constant should match the one in campus.common.schema
PK = "id"


class TableInterface(ABC):
    """Interface for table storage operations."""

    def __init__(self, name: str):
        """Initialize the table interface with a name."""
        self.name = name

    @abstractmethod
    def get_by_id(self, row_id: str) -> dict[str, Any]:
        """Retrieve a row by its ID.

        Args:
            row_id: The ID of the row to retrieve

        Returns:
            The row data as a dictionary

        Raises:
            storage_errors.NotFoundError: If no row exists with the given ID
        """
        ...

    @abstractmethod
    def get_matching(
        self,
        query: dict,
        *,
        order_by: str | None = None,
        ascending: bool = True,
        limit: int | None = None,
        offset: int = 0
    ) -> list[dict]:
        """Retrieve rows matching a query.

        Args:
            query: Dictionary of field to value mappings for filtering.
                   Values can be exact matches or Operator instances for
                   comparison queries (gt, gte, lt, lte).
                   Multiple fields are treated as implicit AND (all conditions must match).
            order_by: Field name to sort by. If None, results are unordered.
            ascending: Sort direction. True for ascending, False for descending.
            limit: Maximum number of rows to return. If None, all matching rows are returned.
            offset: Number of rows to skip before returning results.

        Returns:
            List of matching rows as dictionaries.

        Example:
            # Simple exact match
            get_matching({"user_id": "user_123"})

            # With operators
            get_matching({"duration_ms": gt(1000)})

            # Multiple fields (implicit AND)
            get_matching({"user_id": "user_123", "status": "active"})

            # With sorting and pagination
            get_matching({"status": "active"}, order_by="created_at", ascending=False, limit=10, offset=0)
        """
        ...

    @abstractmethod
    def aggregate(
        self,
        query: dict | None = None,
        *,
        group_by: Sequence[str] = (),
        functions: Mapping[str, Agg],
        bucket: TimeBucket | None = None,
        order_by: Sequence[str] = (),
        limit: int | None = None,
        offset: int = 0
    ) -> list[dict[str, Any]]:
        """Aggregate the rows matching a query.

        Every backend implements the same semantics:

        - Without group_by and bucket, exactly one row of totals is
          returned, even when nothing matches (count is 0, the rest None).
        - With grouping and no matching rows, an empty list is returned.
        - count/count_distinct are ints. sum/avg/min/max/percentile are
          floats, or None when the column has no non-null values in the
          group.
        - percentile is continuous, with linear interpolation between
          neighbouring values (PostgreSQL's percentile_cont); NULLs are
          ignored.
        - Rows are ordered only by order_by, or by bucket when there is
          one and no order_by. A NULL sorts after every other value: last
          ascending, first descending.

        Args:
            query: The filter language of get_matching: exact matches,
                   None (IS NULL) and the operators gt, gte, lt, lte, ne,
                   between, combined as an implicit AND.
            group_by: Column names to group by. Each appears in every
                   result row under its own name. The group_by columns,
                   the bucket alias and the function aliases must be
                   distinct names.
            functions: Aggregation alias to Agg. Each alias is a key in
                   the result rows. At least one function is required.
                   sum, avg, min, max and percentile need a column that
                   holds numbers.
            bucket: Optional time bucketing of bucket.column, reported
                   under bucket.alias as an ISO 8601 UTC string with
                   second precision, such as "2026-11-24T10:17:00+00:00".
                   bucket.column holds ISO 8601 text in UTC (on
                   PostgreSQL, it may also be a TIMESTAMPTZ or TIMESTAMP
                   column).
            order_by: Output names to sort by: a group_by column, a
                   function alias or the bucket alias. A leading "-" sorts
                   descending.
            limit: Maximum number of rows to return. If None, all rows are returned.
            offset: Number of rows to skip before returning results.

        Returns:
            List of result rows as dictionaries.

        Raises:
            ValueError: If any argument is invalid. Raised before the
                database is touched.

        Example:
            # Requests per minute with latency percentiles
            aggregate(
                {"status_code": lt(500)},
                bucket=TimeBucket("started_at", "minute"),
                functions={
                    "requests": Agg("count"),
                    "p50_ms": Agg("percentile", "duration_ms", 0.5),
                    "p99_ms": Agg("percentile", "duration_ms", 0.99),
                },
            )
        """
        ...

    @abstractmethod
    def insert_one(self, row: dict):
        """Insert a row into the specified table."""
        ...

    def insert_many(
            self,
            rows: list[dict],
            *,
            max_retries: int = 1
    ) -> dict[int, Exception]:
        """Insert multiple rows into the specified table.

        This returns a dict of errors encountered, with row index as key
        and exception as value.
        """
        # Concrete implementations may override this method for improved
        # performance with multiple insertions
        if not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be a zero or positive integer")
        # Use a dict as a sparse array for errors to avoid empty indices in list
        errors = {}
        for i, row in enumerate(rows):
            try:
                self.insert_one(row)
            except Exception as e:
                errors[i] = e
        # Happy path
        if not errors:
            return errors
        # Retry while errors remain and max_retries not reached
        retries = 1
        while 0 < retries <= max_retries and errors:
            # Convert to list to avoid RuntimeError: dictionary changed size during iteration
            for i, e in list(errors.items()):
                try:
                    self.insert_one(rows[i])
                except Exception as e:
                    errors[i] = e
                else:
                    del errors[i]
            retries += 1
        return errors

    @abstractmethod
    def update_by_id(self, row_id: str, update: dict):
        """Update a row in the specified table.

        Raises:
            storage_errors.NotFoundError: If no row exists with the given ID
        """
        ...

    @abstractmethod
    def update_matching(self, query: dict, update: dict):
        """Update rows matching a query in the specified table."""
        ...

    @abstractmethod
    def delete_by_id(self, row_id: str):
        """Delete a row from the specified table.

        Raises:
            storage_errors.NotFoundError: If no row exists with the given ID
        """
        ...

    @abstractmethod
    def delete_matching(self, query: dict):
        """Delete rows matching a query in the specified table."""
        ...

    @abstractmethod
    def init_from_model(self, name: str, model: type[InternalModel | Model]) -> None:
        """Initialize the table from a Campus model definition."""
        ...

    @abstractmethod
    def init_from_schema(self, schema: str) -> None:
        """Initialize the table with the given SQL schema.

        Both backends implement this; production-schema management
        conventions may restrict its use (see backend implementations).
        """
        ...
