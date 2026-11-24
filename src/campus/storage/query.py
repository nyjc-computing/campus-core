"""campus.storage.query

This module provides query operators for storage filtering.

These operators enable more expressive queries while maintaining backward
compatibility with simple exact-match queries.

Example:
    from campus.storage.query import gt, gte, lt, lte, ne, between

    # Simple exact match (backward compatible)
    storage.get_matching({"user_id": "user_123"})

    # With operators
    storage.get_matching({"duration_ms": gt(1000)})
    storage.get_matching({"started_at": gte("2024-01-01")})
    storage.get_matching({"status_code": lt(500)})
    storage.get_matching({"id": ne("@metadata")})
    storage.get_matching({"started_at": between("2024-01-01", "2024-12-31")})

Aggregation queries (TableInterface.aggregate) take the same filters plus
Agg (what to compute) and TimeBucket (how to group a timestamp column):

    from campus.storage.query import Agg, TimeBucket

    storage.aggregate(
        {"started_at": gte("2026-11-01T00:00:00+00:00")},
        bucket=TimeBucket("started_at", "minute"),
        functions={
            "requests": Agg("count"),
            "p99_ms": Agg("percentile", "duration_ms", 0.99),
        },
    )
"""

from dataclasses import dataclass
from typing import Any, NamedTuple


@dataclass(frozen=True)
class Operator:
    """Base class for query operators.

    Operators are immutable value objects that represent comparison
    operations in queries.
    """
    value: Any


class gt(Operator):
    """Greater than comparison.

    Example:
        {"duration_ms": gt(1000)}  # duration_ms > 1000
    """


class gte(Operator):
    """Greater than or equal comparison.

    Example:
        {"started_at": gte("2024-01-01")}  # started_at >= "2024-01-01"
    """


class lt(Operator):
    """Less than comparison.

    Example:
        {"timeout": lt(5000)}  # timeout < 5000
    """


class lte(Operator):
    """Less than or equal comparison.

    Example:
        {"retries": lte(3)}  # retries <= 3
    """


class ne(Operator):
    """Not equal comparison.

    Example:
        {"id": ne("@metadata")}  # id != "@metadata"
    """


@dataclass(frozen=True)
class between(Operator):
    """Range comparison for inclusive lower and upper bounds.

    Example:
        {"started_at": between("2024-01-01", "2024-12-31")}  # 2024-01-01 <= started_at <= 2024-12-31

    Attributes:
        value: A tuple of (min_value, max_value) representing the inclusive range
    """
    value: tuple[Any, Any]

    def __init__(self, min_value: Any, max_value: Any):
        # Use object.__setattr__ since dataclass is frozen
        object.__setattr__(self, "value", (min_value, max_value))


def is_operator(value: Any) -> bool:
    """Check if a value is a query operator.

    Args:
        value: The value to check

    Returns:
        True if the value is an Operator instance, False otherwise
    """
    return isinstance(value, Operator)


class Agg(NamedTuple):
    """An aggregation that TableInterface.aggregate computes over a column.

    Attributes:
        func: The aggregation: "count", "count_distinct", "sum", "avg",
            "min", "max" or "percentile".
        column: The column to aggregate. None is only valid for "count"
            and counts rows (COUNT(*)); with a column, "count" counts
            non-null values. Every other func requires a column, whose
            values must be numeric.
        fraction: The percentile to compute, between 0 and 1. Required by
            "percentile" and ignored by every other func.

    Example:
        Agg("count")                        # COUNT(*)
        Agg("count", "duration_ms")         # non-null duration_ms values
        Agg("percentile", "duration_ms", 0.99)
    """
    func: str
    column: str | None = None
    fraction: float | None = None


class TimeBucket(NamedTuple):
    """A grouping of a timestamp column into fixed-width UTC time buckets.

    The timestamp column holds ISO 8601 text in UTC (or, on PostgreSQL, a
    native TIMESTAMPTZ/TIMESTAMP column). Each bucket is reported as its
    start time, an ISO 8601 UTC string with second precision such as
    "2026-11-24T10:17:00+00:00".

    Attributes:
        column: The timestamp column to bucket.
        width: The bucket width: "minute", "hour", "day", "week" or
            "month". Buckets start on UTC boundaries; a week starts on
            Monday 00:00 UTC.
        alias: The result-row key that holds the bucket.

    Example:
        TimeBucket("started_at", "hour")
        TimeBucket("started_at", "day", alias="day")
    """
    column: str
    width: str
    alias: str = "bucket"
