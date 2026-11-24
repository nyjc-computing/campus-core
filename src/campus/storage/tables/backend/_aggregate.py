"""campus.storage.tables.backend._aggregate

Backend-neutral pieces of ``TableInterface.aggregate()``, shared by the
postgres and sqlite backends (nyjc-computing/campus#1243):

- ``validate`` turns the arguments into an ``AggregatePlan``, or raises
  ``ValueError`` before any database work. Both backends call it first,
  so they reject exactly the same inputs.
- ``render`` assembles the SELECT / GROUP BY / ORDER BY clauses around
  backend-specific SQL expressions, so both run the same statement shape.
- ``function_expression`` builds the SQL of one aggregation.
- ``percentile`` is the continuous percentile: what PostgreSQL's
  ``percentile_cont`` computes, and what SQLite computes in Python
  through ``Percentile``.
- ``normalise`` gives the function results their documented types.

Nothing here talks to a database or imports a driver.
"""

import math
import re
import typing
from collections.abc import Mapping, Sequence
from typing import Any

from campus.storage.query import Agg, TimeBucket

# Identifiers reach SQL only as double-quoted names that match this, so a
# column, alias or query key can never carry SQL syntax.
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
AGGREGATIONS = ("count", "count_distinct", "sum", "avg", "min", "max", "percentile")
BUCKET_WIDTHS = ("minute", "hour", "day", "week", "month")


class AggregatePlan(typing.NamedTuple):
    """Validated ``aggregate()`` arguments, normalised for the backends.

    Attributes:
        group_by: The grouping columns.
        functions: (alias, Agg) pairs in the caller's order; an Agg's
            fraction is a float when its func is "percentile", else None.
        bucket: The time bucketing, or None.
        order_by: (output name, ascending) pairs; with a bucket and no
            order_by of the caller's, the bucket ascending.
        limit: The row limit, or None for no limit.
        offset: The number of rows to skip.
        output_names: Every key of a result row, in order: the group_by
            columns, then the bucket alias, then the function aliases.
    """
    group_by: tuple[str, ...]
    functions: tuple[tuple[str, Agg], ...]
    bucket: TimeBucket | None
    order_by: tuple[tuple[str, bool], ...]
    limit: int | None
    offset: int
    output_names: tuple[str, ...]


def _identifier(value: Any, where: str) -> str:
    """Return value when it is a valid identifier, else raise ValueError."""
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError(
            f"aggregate(): {where} {value!r} is not a valid name "
            f"(expected ^[A-Za-z_][A-Za-z0-9_]*$)"
        )
    return value


def _columns(names: Any, argument: str) -> tuple[str, ...]:
    """Validate a sequence of column names (a bare str is a common mistake)."""
    if isinstance(names, (str, bytes)) or not isinstance(names, Sequence):
        raise ValueError(f"aggregate(): {argument} must be a sequence of column names")
    return tuple(_identifier(name, f"{argument} entry") for name in names)


def _count(value: Any, argument: str, *, optional: bool = False) -> int | None:
    """Validate a non-negative integer (None is allowed when optional)."""
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        expected = "None or a non-negative integer" if optional else "a non-negative integer"
        raise ValueError(f"aggregate(): {argument} must be {expected}, got {value!r}")
    return value


def validate(
    table: str,
    query: Mapping[str, Any] | None,
    group_by: Sequence[str],
    functions: Mapping[str, Agg],
    bucket: TimeBucket | None,
    order_by: Sequence[str],
    limit: int | None,
    offset: int,
) -> AggregatePlan:
    """Validate aggregate() arguments and normalise them into a plan.

    Args:
        table: Name of the table.
        query: The filter dictionary (the language of get_matching).
        group_by: Column names to group by.
        functions: Aggregation alias to Agg; at least one.
        bucket: The time bucketing, or None.
        order_by: Output names to sort by; a leading "-" sorts descending.
        limit: Maximum number of rows, or None.
        offset: Number of rows to skip.

    Returns:
        The plan the backends build their statement from.

    Raises:
        ValueError: If any argument is invalid. Nothing is raised for a
            column that does not exist in the table: only the database
            can know that, so it surfaces as a storage error instead.
    """
    _identifier(table, "table name")

    if query is not None:
        if not isinstance(query, Mapping):
            raise ValueError("aggregate(): query must be a dict of column to value or operator")
        for key in query:
            _identifier(key, "query column")
    columns = _columns(group_by, "group_by")

    if bucket is not None:
        if not isinstance(bucket, TimeBucket):
            raise ValueError("aggregate(): bucket must be a TimeBucket")
        if bucket.width not in BUCKET_WIDTHS:
            raise ValueError(
                f"aggregate(): bucket.width {bucket.width!r} is not a time bucket "
                f"(expected one of: {', '.join(BUCKET_WIDTHS)})"
            )
        _identifier(bucket.column, "bucket.column")
        _identifier(bucket.alias, "bucket.alias")

    if not isinstance(functions, Mapping) or not functions:
        raise ValueError("aggregate(): functions must hold at least one alias-to-Agg entry")
    aggregations: list[tuple[str, Agg]] = []
    for alias, agg in functions.items():
        _identifier(alias, "functions alias")
        if not isinstance(agg, Agg):
            raise ValueError(
                f"aggregate(): functions[{alias!r}] must be an Agg, got {type(agg).__name__}"
            )
        if agg.func not in AGGREGATIONS:
            raise ValueError(
                f"aggregate(): functions[{alias!r}].func {agg.func!r} is not an aggregation "
                f"(expected one of: {', '.join(AGGREGATIONS)})"
            )
        column = agg.column
        if column is None and agg.func != "count":
            raise ValueError(f"aggregate(): functions[{alias!r}] ({agg.func}) requires a column")
        if column is not None:
            column = _identifier(column, f"functions[{alias!r}].column")
        fraction = None
        if agg.func == "percentile":
            if (
                isinstance(agg.fraction, bool)
                or not isinstance(agg.fraction, (int, float))
                or not 0 <= agg.fraction <= 1
            ):
                raise ValueError(
                    f"aggregate(): functions[{alias!r}] (percentile) requires a fraction "
                    f"between 0 and 1, got {agg.fraction!r}"
                )
            fraction = float(agg.fraction)
        aggregations.append((alias, Agg(agg.func, column, fraction)))

    output_names = columns
    if bucket is not None:
        output_names += (bucket.alias,)
    output_names += tuple(alias for alias, _ in aggregations)
    if len(set(output_names)) != len(output_names):
        duplicated = sorted({name for name in output_names if output_names.count(name) > 1})
        raise ValueError(
            f"aggregate(): output names must be distinct, so that every result key has "
            f"one meaning; duplicated: {', '.join(duplicated)}"
        )

    if isinstance(order_by, (str, bytes)) or not isinstance(order_by, Sequence):
        raise ValueError("aggregate(): order_by must be a sequence of output names")
    sorting: list[tuple[str, bool]] = []
    for name in order_by:
        descending = isinstance(name, str) and name.startswith("-")
        target = name[1:] if descending else name
        if target not in output_names:
            raise ValueError(
                f"aggregate(): order_by {name!r} is not an output name "
                f"(expected one of: {', '.join(output_names)})"
            )
        sorting.append((target, not descending))
    if bucket is not None and not sorting:
        sorting = [(bucket.alias, True)]

    return AggregatePlan(
        group_by=columns,
        functions=tuple(aggregations),
        bucket=bucket,
        order_by=tuple(sorting),
        limit=_count(limit, "limit", optional=True),
        offset=_count(offset, "offset"),
        output_names=output_names,
    )


def function_expression(agg: Agg, percentile: str, params: list) -> str:
    """Build the SQL of one aggregation.

    Args:
        agg: The aggregation; its column and fraction come from a plan.
        percentile: The backend's SQL for the continuous percentile: a
            "{}" that takes the column (double-quoted by the template),
            and one placeholder for the fraction.
        params: The statement's parameters, appended to in SQL order.

    Returns:
        The SQL expression; every identifier in it is validated and
        double-quoted, and the fraction is bound through params.
    """
    column = agg.column
    if agg.func == "percentile":
        params.append(agg.fraction)
        return percentile.format(column)
    if agg.func == "count":
        return "COUNT(*)" if column is None else f'COUNT("{column}")'
    if agg.func == "count_distinct":
        return f'COUNT(DISTINCT "{column}")'
    return f'{agg.func.upper()}("{column}")'


def render(plan: AggregatePlan, expressions: Sequence[str]) -> tuple[str, str]:
    """Assemble the SELECT list and the GROUP BY / ORDER BY clauses of a plan.

    Args:
        plan: The validated arguments.
        expressions: One SQL expression per output column, in output order.

    Returns:
        The SELECT list (every expression aliased to its output name) and
        the clause that follows the WHERE clause: "" without grouping,
        else " GROUP BY ... [ORDER BY ...]".

    Grouping and sorting name their columns by output position, so an
    output name is never mistaken for a column of the table.
    """
    select_list = ", ".join(
        f'{expression} AS "{name}"'
        for expression, name in zip(expressions, plan.output_names, strict=True)
    )
    positions = {name: position for position, name in enumerate(plan.output_names, start=1)}
    grouped = [str(positions[column]) for column in plan.group_by]
    if plan.bucket is not None:
        grouped.append(str(positions[plan.bucket.alias]))
    clauses = []
    if grouped:
        clauses.append(f"GROUP BY {', '.join(grouped)}")
    if plan.order_by:
        # Where NULL sorts is spelled out: the defaults are opposite
        # (PostgreSQL orders it after every value, SQLite before them).
        directions = ", ".join(
            f"{positions[name]} {'ASC NULLS LAST' if ascending else 'DESC NULLS FIRST'}"
            for name, ascending in plan.order_by
        )
        clauses.append(f"ORDER BY {directions}")
    return select_list, f" {' '.join(clauses)}" if clauses else ""


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Compute the continuous percentile with linear interpolation.

    This is PostgreSQL's ``percentile_cont``: with the n values sorted,
    the result lies at rank ``fraction * (n - 1)``, interpolated linearly
    between the two neighbouring values.

    Args:
        values: The non-null values of one group, in any order.
        fraction: The percentile, between 0 and 1.

    Returns:
        The percentile, or None when there are no values.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = fraction * (len(ordered) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return float(ordered[lower])
    return ordered[lower] + (rank - lower) * (ordered[upper] - ordered[lower])


class Percentile:
    """The sqlite3 aggregate ``campus_percentile(fraction, value)``.

    Computes :func:`percentile` over the values of a group. sqlite3 hands
    NULL to ``step()`` just as it does to built-in aggregates, so they are
    skipped here; a group with no value then yields None, like the other
    aggregates over an empty set.
    """

    def __init__(self) -> None:
        self._values: list[float] = []
        self._fraction = 0.0

    def step(self, fraction: float, value: Any) -> None:
        """Collect one row's value."""
        if value is not None:
            self._values.append(float(value))
            self._fraction = fraction

    def finalize(self) -> float | None:
        """Return the percentile of the collected values."""
        return percentile(self._values, self._fraction)


def normalise(plan: AggregatePlan, row: dict[str, Any]) -> dict[str, Any]:
    """Give the function results of one result row their documented types.

    Args:
        plan: The plan the row was produced from.
        row: A deserialised result row; modified in place.

    Returns:
        The row, with count/count_distinct as int and every other
        function as float or None.

    Raises:
        ValueError: If a function produced a value that is not a number:
            sum, avg, min, max and percentile need a numeric column.
    """
    for alias, agg in plan.functions:
        if agg.func in ("count", "count_distinct"):
            continue
        value = row[alias]
        if value is not None and type(value) is not float:
            try:
                row[alias] = float(value)
            except (TypeError, ValueError):
                raise ValueError(
                    f"aggregate(): '{alias}' did not produce a number; "
                    f"sum, avg, min, max and percentile need a numeric column"
                ) from None
    return row
