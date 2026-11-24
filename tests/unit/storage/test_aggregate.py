"""Tests for TableInterface.aggregate() (nyjc-computing/campus#1243).

One suite of cases, AggregateCases, runs against SQLiteTable (always) and
against PostgreSQLTable when CAMPUS_TEST_POSTGRES_URI names a throwaway
PostgreSQL database. Without the variable every PostgreSQL case is
skipped, with a message saying how to enable it:

    docker run -d --rm --name campus-core-agg-pg -e POSTGRES_PASSWORD=test \\
        -e POSTGRES_DB=campus_test -p 55432:5432 postgres:16-alpine
    export CAMPUS_TEST_POSTGRES_URI=postgresql://postgres:test@localhost:55432/campus_test

The PostgreSQL fixture hands that URI to the backend through
POSTGRESDB_URI, the variable it reads, for the lifetime of the class, and
drops the table it created afterwards. The tests create and drop tables
in that database, so it must hold nothing else.
"""

import os
import sqlite3
import statistics
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from unittest import mock

from campus.common import env

# Configure test storage before importing storage modules
env.set('STORAGE_MODE', "1")

from campus.storage import (  # noqa: E402
    Agg,
    StorageError,
    TimeBucket,
    between,
    gt,
    gte,
    lt,
    lte,
    ne,
)

POSTGRES_URI_VAR = "CAMPUS_TEST_POSTGRES_URI"
SKIP_POSTGRES = (
    f"{POSTGRES_URI_VAR} is not set: point it at a throwaway PostgreSQL to run the live "
    "aggregate tests, e.g. docker run -d --rm --name campus-core-agg-pg -e POSTGRES_PASSWORD=test "
    "-e POSTGRES_DB=campus_test -p 55432:5432 postgres:16-alpine, then export "
    f"{POSTGRES_URI_VAR}=postgresql://postgres:test@localhost:55432/campus_test "
    "(nyjc-computing/campus#1243)"
)

COUNT = Agg("count")

# Every function, by a short alias.
ALL_FUNCTIONS = {
    "n": COUNT,
    "c": Agg("count", "duration_ms"),
    "d": Agg("count_distinct", "user_id"),
    "s": Agg("sum", "duration_ms"),
    "a": Agg("avg", "duration_ms"),
    "mn": Agg("min", "duration_ms"),
    "mx": Agg("max", "duration_ms"),
    "p50": Agg("percentile", "duration_ms", 0.5),
}
# The same functions over a column that has no value in the matching rows.
ALL_NONE = {"s": None, "a": None, "mn": None, "mx": None, "p50": None}

# The fixture rows: (id, started_at in UTC, duration_ms, status_code, route, user_id, tags).
# They span a week boundary (Sunday 2026-11-01 / Monday 2026-11-02), a month boundary
# (2026-10-31 / 2026-11-01) and the year boundary, and carry NULLs in duration_ms,
# user_id and tags.
ROWS = [
    ("r01", "2025-12-31T23:59:59+00:00", 100, 200, "/api/launch", "u1", None),
    ("r02", "2026-01-01T00:00:01+00:00", 200, 200, "/api/launch", "u1", ["api"]),
    ("r03", "2026-10-31T23:59:59+00:00", 300, 500, "/api/traces", "u2", ["traces"]),
    ("r04", "2026-11-01T00:00:00+00:00", None, 200, "/api/traces", "u2", ["traces"]),
    ("r05", "2026-11-01T23:59:59+00:00", 500, 200, "/api/launch", None, ["api"]),
    ("r06", "2026-11-02T00:00:00+00:00", 600, 404, "/api/traces", "u1", ["traces", "error"]),
    ("r07", "2026-11-02T10:17:30+00:00", 700, 200, "/api/launch", "u1", ["api"]),
    ("r08", "2026-11-02T10:17:59+00:00", 800, 500, "/api/traces", "u2", ["traces", "error"]),
    ("r09", "2026-11-02T10:18:00+00:00", 900, 200, "/api/launch", "u2", ["api"]),
    ("r10", "2026-11-03T10:17:00+00:00", 1000, 200, "/api/traces", "u1", None),
]
COLUMNS = ("id", "started_at", "duration_ms", "status_code", "route", "user_id", "tags")

# Minute buckets of all ten rows: two spans share 10:17.
MINUTE_BUCKETS = [
    ("2025-12-31T23:59:00+00:00", 1),
    ("2026-01-01T00:00:00+00:00", 1),
    ("2026-10-31T23:59:00+00:00", 1),
    ("2026-11-01T00:00:00+00:00", 1),
    ("2026-11-01T23:59:00+00:00", 1),
    ("2026-11-02T00:00:00+00:00", 1),
    ("2026-11-02T10:17:00+00:00", 2),
    ("2026-11-02T10:18:00+00:00", 1),
    ("2026-11-03T10:17:00+00:00", 1),
]

# (what is wrong, a fragment of the message, the aggregate() arguments)
INVALID = [
    ("group_by column", "group_by entry .* is not a valid name",
     {'functions': {"n": COUNT}, 'group_by': ["route", "a-b"]}),
    ("group_by given as a bare string", "group_by must be a sequence",
     {'functions': {"n": COUNT}, 'group_by': "route"}),
    ("function alias", "functions alias .* is not a valid name",
     {'functions': {"total ms": Agg("sum", "duration_ms")}}),
    ("function column", r"functions\['n'\]\.column .* is not a valid name",
     {'functions': {"n": Agg("count", "1duration")}}),
    ("query column", "query column .* is not a valid name",
     {'functions': {"n": COUNT}, 'query': {'id" OR "1"="1': "r01"}}),
    ("bucket column", "bucket.column .* is not a valid name",
     {'functions': {"n": COUNT}, 'bucket': TimeBucket("started_at;", "day")}),
    ("bucket alias", "bucket.alias .* is not a valid name",
     {'functions': {"n": COUNT}, 'bucket': TimeBucket("started_at", "day", alias="9am")}),
    ("unknown func", r"functions\['x'\]\.func 'median' is not an aggregation",
     {'functions': {"x": Agg("median", "duration_ms")}}),
    ("count_distinct without a column", r"\(count_distinct\) requires a column",
     {'functions': {"x": Agg("count_distinct")}}),
    ("sum without a column", r"\(sum\) requires a column", {'functions': {"x": Agg("sum")}}),
    ("avg without a column", r"\(avg\) requires a column", {'functions': {"x": Agg("avg")}}),
    ("min without a column", r"\(min\) requires a column", {'functions': {"x": Agg("min")}}),
    ("max without a column", r"\(max\) requires a column", {'functions': {"x": Agg("max")}}),
    ("percentile without a column", r"\(percentile\) requires a column",
     {'functions': {"x": Agg("percentile")}}),
    ("percentile without a fraction", r"\(percentile\) requires a fraction",
     {'functions': {"x": Agg("percentile", "duration_ms")}}),
    ("percentile fraction above 1", r"requires a fraction between 0 and 1, got 1.5",
     {'functions': {"x": Agg("percentile", "duration_ms", 1.5)}}),
    ("percentile fraction below 0", r"requires a fraction between 0 and 1, got -0.5",
     {'functions': {"x": Agg("percentile", "duration_ms", -0.5)}}),
    ("percentile fraction not a number", r"requires a fraction between 0 and 1, got '0.5'",
     {'functions': {"x": Agg("percentile", "duration_ms", "0.5")}}),
    ("empty functions", "at least one", {'functions': {}}),
    ("functions not a mapping", "at least one", {'functions': [COUNT]}),
    ("functions value that is not an Agg", r"functions\['x'\] must be an Agg",
     {'functions': {"x": ("count", None, None)}}),
    ("unknown bucket width", "is not a time bucket",
     {'functions': {"n": COUNT}, 'bucket': TimeBucket("started_at", "year")}),
    ("bucket that is not a TimeBucket", "bucket must be a TimeBucket",
     {'functions': {"n": COUNT}, 'bucket': ("started_at", "day")}),
    ("order_by on a column that is not an output", "order_by 'started_at' is not an output name",
     {'functions': {"n": COUNT}, 'group_by': ["route"], 'order_by': ["started_at"]}),
    ("order_by with a typo", "order_by '-requets' is not an output name",
     {'functions': {"requests": COUNT}, 'order_by': ["-requets"]}),
    ("order_by given as a bare string", "order_by must be a sequence",
     {'functions': {"n": COUNT}, 'order_by': "n"}),
    ("negative limit", "limit must be None or a non-negative integer, got -1",
     {'functions': {"n": COUNT}, 'limit': -1}),
    ("non-integer limit", "limit must be None or a non-negative integer, got 2.5",
     {'functions': {"n": COUNT}, 'limit': 2.5}),
    ("boolean limit", "limit must be None or a non-negative integer, got True",
     {'functions': {"n": COUNT}, 'limit': True}),
    ("negative offset", "offset must be a non-negative integer, got -1",
     {'functions': {"n": COUNT}, 'offset': -1}),
    ("non-integer offset", "offset must be a non-negative integer, got '1'",
     {'functions': {"n": COUNT}, 'offset': "1"}),
    ("boolean offset", "offset must be a non-negative integer, got False",
     {'functions': {"n": COUNT}, 'offset': False}),
    ("query that is not a dict", "query must be a dict",
     {'functions': {"n": COUNT}, 'query': [("id", "r01")]}),
    ("repeated group_by column", "output names must be distinct",
     {'functions': {"n": COUNT}, 'group_by': ["route", "route"]}),
    ("alias equal to a group_by column", "output names must be distinct",
     {'functions': {"route": COUNT}, 'group_by': ["route"]}),
    ("alias equal to the bucket alias", "output names must be distinct",
     {'functions': {"n": COUNT}, 'bucket': TimeBucket("started_at", "day", alias="n")}),
]

# Names that must never reach SQL: quoting, a qualified name, emptiness, a leading
# digit, separators and a non-ASCII letter.
BAD_NAMES = ['x" OR 1=1 --', "", "1abc", "a-b", "a.b", "a b", "é", "id;DROP TABLE t"]


class AggregateCases:
    """The backend-agnostic aggregate() cases.

    Not a TestCase: each backend mixes it into its own TestCase and
    supplies make_table() and drop_table(). Every case is a read, so one
    populated table per class serves them all. Where SQL leaves the order
    of rows open (grouping without order_by), a case either sorts
    explicitly or compares without order.
    """

    TABLE = "test_aggregate_spans"
    STARTED_AT_DDL = "TEXT NOT NULL"
    # The method that opens a connection: patching it proves that
    # validation raises before the database is touched.
    CONNECTION_METHOD = None

    @staticmethod
    def started_at(value):
        """The value the fixture stores for a UTC timestamp."""
        return value

    @classmethod
    def rows(cls):
        """The fixture rows as dictionaries, started_at as this class stores it."""
        return [
            dict(zip(COLUMNS, row, strict=True), started_at=cls.started_at(row[1]))
            for row in ROWS
        ]

    @classmethod
    def setUpClass(cls):
        """Create and populate the table, dropping anything a failed run left."""
        super().setUpClass()
        cls.drop_table(cls.TABLE)
        cls.table = cls.make_table(cls.TABLE)
        cls.table.init_from_schema(
            f'CREATE TABLE IF NOT EXISTS "{cls.TABLE}" ('
            '"id" TEXT PRIMARY KEY, '
            f'"started_at" {cls.STARTED_AT_DDL}, '
            '"duration_ms" INTEGER, '
            '"status_code" INTEGER NOT NULL, '
            '"route" TEXT NOT NULL, '
            '"user_id" TEXT, '
            '"tags" TEXT);'
        )
        errors = cls.table.insert_many(cls.rows())
        if errors:
            raise AssertionError(f"fixture rows were not inserted: {errors}")

    @classmethod
    def tearDownClass(cls):
        """Remove the table."""
        cls.drop_table(cls.TABLE)

    def buckets(self, bucket, query=None, **arguments):
        """Run a bucketed row count and return [(bucket, rows)] in row order."""
        rows = self.table.aggregate(query, functions={"n": COUNT}, bucket=bucket, **arguments)
        return [(row[bucket.alias], row["n"]) for row in rows]

    # ---- Shape of the result -------------------------------------------------

    def test_totals_without_grouping(self):
        """Without grouping: one row, every function under its alias, in order."""
        rows = self.table.aggregate(functions={
            "requests": COUNT,
            "timed": Agg("count", "duration_ms"),
            "distinct_users": Agg("count_distinct", "user_id"),
            "total_ms": Agg("sum", "duration_ms"),
            "mean_ms": Agg("avg", "duration_ms"),
            "fastest_ms": Agg("min", "duration_ms"),
            "slowest_ms": Agg("max", "duration_ms"),
        })
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(list(row), [
            "requests", "timed", "distinct_users", "total_ms", "mean_ms", "fastest_ms", "slowest_ms",
        ])
        self.assertEqual((row["requests"], row["timed"], row["distinct_users"]), (10, 9, 2))
        self.assertEqual((row["total_ms"], row["fastest_ms"], row["slowest_ms"]), (5100.0, 100.0, 1000.0))
        self.assertAlmostEqual(row["mean_ms"], 5100 / 9, places=12)

    def test_totals_are_one_row_even_when_nothing_matches(self):
        """No match and no grouping: one row, count 0 and the rest None."""
        rows = self.table.aggregate({"route": "/api/none"}, functions=ALL_FUNCTIONS)
        self.assertEqual(rows, [{"n": 0, "c": 0, "d": 0, **ALL_NONE}])

    def test_totals_ignore_limit_and_offset_that_keep_the_row(self):
        """limit and offset apply to result rows: here the single totals row."""
        functions = {"n": COUNT}
        self.assertEqual(self.table.aggregate(functions=functions, limit=5), [{"n": 10}])
        self.assertEqual(self.table.aggregate(functions=functions, limit=0), [])

    def test_result_types(self):
        """Counts are ints; every other function is a float or None."""
        row = self.table.aggregate(functions=ALL_FUNCTIONS)[0]
        for alias in ("n", "c", "d"):
            self.assertIs(type(row[alias]), int, alias)
        for alias in ALL_NONE:
            self.assertIs(type(row[alias]), float, alias)
        empty = self.table.aggregate({"route": "/api/none"}, functions=ALL_FUNCTIONS)[0]
        for alias in ALL_NONE:
            self.assertIsNone(empty[alias], alias)
        for row in self.table.aggregate(group_by=["route"], functions=ALL_FUNCTIONS):
            for alias in ALL_NONE:
                self.assertIs(type(row[alias]), float, alias)

    # ---- Grouping ------------------------------------------------------------

    def test_group_by_single_column(self):
        """Each group is one row, with the grouped column under its own name."""
        rows = self.table.aggregate(group_by=["route"], functions={"n": COUNT})
        self.assertEqual(list(rows[0]), ["route", "n"])
        self.assertEqual({row["route"]: row["n"] for row in rows}, {"/api/launch": 5, "/api/traces": 5})

    def test_group_by_two_columns(self):
        """Grouping by two columns pairs them up."""
        rows = self.table.aggregate(
            group_by=["route", "status_code"], functions={"n": COUNT},
            order_by=["route", "status_code"])
        self.assertEqual(
            [(row["route"], row["status_code"], row["n"]) for row in rows],
            [("/api/launch", 200, 5), ("/api/traces", 200, 2), ("/api/traces", 404, 1),
             ("/api/traces", 500, 2)])

    def test_group_by_keeps_a_null_group(self):
        """Rows whose grouped column is NULL form a group of their own."""
        rows = self.table.aggregate(group_by=["user_id"], functions={"n": COUNT})
        self.assertEqual({row["user_id"]: row["n"] for row in rows}, {"u1": 5, "u2": 4, None: 1})

    def test_group_values_come_back_as_stored(self):
        """A grouped column comes back with the value and type get_matching returns."""
        columns = ["route", "status_code", "user_id"]
        rows = self.table.aggregate(functions={"n": COUNT}, group_by=columns, order_by=columns)
        stored = {tuple(row[column] for column in columns) for row in self.table.get_matching({})}
        self.assertEqual({tuple(row[column] for column in columns) for row in rows}, stored)
        self.assertEqual(len(rows), len(stored))
        self.assertTrue(all(type(row["status_code"]) is int for row in rows))

    def test_group_by_decodes_json_text_values(self):
        """A grouped column holding JSON text is decoded like a get_matching row."""
        rows = self.table.aggregate(group_by=["tags"], functions={"n": COUNT})
        counted = {}
        for row in rows:
            self.assertIsInstance(row["tags"], (list, type(None)))
            counted[tuple(row["tags"]) if row["tags"] is not None else None] = row["n"]
        self.assertEqual(counted, {None: 2, ("api",): 4, ("traces",): 2, ("traces", "error"): 2})

    def test_grouped_with_no_matching_rows_is_empty(self):
        """No match with grouping: no groups, so no rows."""
        functions = {"n": COUNT}
        self.assertEqual(self.table.aggregate({"route": "/api/none"}, group_by=["route"],
                                              functions=functions), [])
        self.assertEqual(self.table.aggregate({"route": "/api/none"}, group_by=["route"],
                                              functions=functions, limit=5), [])

    # ---- Functions -----------------------------------------------------------

    def test_count_counts_rows_and_non_null_values(self):
        """count with no column is COUNT(*); with a column it skips NULLs."""
        rows = self.table.aggregate(functions={"rows": COUNT, "timed": Agg("count", "duration_ms")})
        self.assertEqual((rows[0]["rows"], rows[0]["timed"]), (10, 9))

    def test_count_distinct_ignores_nulls(self):
        """count_distinct counts each distinct non-null value once."""
        rows = self.table.aggregate(functions={
            "routes": Agg("count_distinct", "route"),
            "users": Agg("count_distinct", "user_id"),
            "tags": Agg("count_distinct", "tags"),
        })
        self.assertEqual((rows[0]["routes"], rows[0]["users"], rows[0]["tags"]), (2, 2, 3))

    def test_functions_are_none_when_the_column_has_no_value(self):
        """With no non-null value to work on, the functions return None."""
        row = self.table.aggregate({"id": "r04"}, functions=ALL_FUNCTIONS)[0]
        self.assertEqual(row, {"n": 1, "c": 0, "d": 1, **ALL_NONE})

    def test_percentile_at_zero_half_and_one(self):
        """p0 is the smallest value, p100 the largest, p50 the middle of nine."""
        rows = self.table.aggregate(functions={
            "p0": Agg("percentile", "duration_ms", 0),
            "p50": Agg("percentile", "duration_ms", 0.5),
            "p100": Agg("percentile", "duration_ms", 1),
        })
        self.assertEqual((rows[0]["p0"], rows[0]["p50"], rows[0]["p100"]), (100.0, 600.0, 1000.0))

    def test_percentile_does_not_interpolate_an_odd_count(self):
        """Nine values (100..1000 by 100): quarter marks land exactly on values."""
        rows = self.table.aggregate(functions={
            "p25": Agg("percentile", "duration_ms", 0.25),
            "p75": Agg("percentile", "duration_ms", 0.75),
            "p99": Agg("percentile", "duration_ms", 0.99),
        })
        self.assertEqual((rows[0]["p25"], rows[0]["p75"]), (300.0, 800.0))
        self.assertAlmostEqual(rows[0]["p99"], 992.0, places=9)

    def test_percentile_interpolates_an_even_count(self):
        """Six values (100, 200, 500, 700, 900, 1000): linear interpolation."""
        rows = self.table.aggregate({"status_code": 200}, functions={
            "p25": Agg("percentile", "duration_ms", 0.25),
            "p50": Agg("percentile", "duration_ms", 0.5),
            "p75": Agg("percentile", "duration_ms", 0.75),
        })
        self.assertEqual((rows[0]["p25"], rows[0]["p50"], rows[0]["p75"]), (275.0, 600.0, 850.0))

    def test_percentile_ignores_nulls(self):
        """A NULL takes no rank: the same six values whether or not r04 is NULL."""
        rows = self.table.aggregate({"status_code": 200}, functions={
            "n": COUNT,
            "p50": Agg("percentile", "duration_ms", 0.5),
            "p100": Agg("percentile", "duration_ms", 1),
        })
        self.assertEqual((rows[0]["n"], rows[0]["p50"], rows[0]["p100"]), (7, 600.0, 1000.0))

    def test_percentile_of_a_single_value(self):
        """One value: every percentile is that value."""
        rows = self.table.aggregate({"status_code": 404}, functions={
            "p0": Agg("percentile", "duration_ms", 0),
            "p50": Agg("percentile", "duration_ms", 0.5),
            "p100": Agg("percentile", "duration_ms", 1)})
        self.assertEqual(rows, [{"p0": 600.0, "p50": 600.0, "p100": 600.0}])

    def test_functions_per_group(self):
        """Every function is computed within each group."""
        rows = self.table.aggregate(
            group_by=["route"], order_by=["route"],
            functions={"n": COUNT, "s": Agg("sum", "duration_ms"), "mx": Agg("max", "duration_ms"),
                       "p50": Agg("percentile", "duration_ms", 0.5)})
        self.assertEqual(rows, [
            {"route": "/api/launch", "n": 5, "s": 2400.0, "mx": 900.0, "p50": 500.0},
            {"route": "/api/traces", "n": 5, "s": 2700.0, "mx": 1000.0, "p50": 700.0},
        ])

    # ---- Filters (the language of get_matching) ------------------------------

    def test_filter_between_is_inclusive(self):
        """between keeps both bounds."""
        rows = self.table.aggregate(
            {"started_at": between("2026-11-01T00:00:00+00:00", "2026-11-02T00:00:00+00:00")},
            functions={"n": COUNT})
        self.assertEqual(rows[0]["n"], 3)

    def test_filter_gte_and_lte(self):
        """gte and lte on two columns combine as an implicit AND."""
        rows = self.table.aggregate(
            {"started_at": gte("2026-11-02T00:00:00+00:00"), "duration_ms": lte(800)},
            functions={"n": COUNT})
        self.assertEqual(rows[0]["n"], 3)

    def test_filter_gt_lt_and_ne(self):
        """gt, lt and ne behave as in get_matching, and never match NULL."""
        def matching(query):
            return self.table.aggregate(query, functions={"n": COUNT})[0]["n"]

        self.assertEqual(matching({"duration_ms": gt(700)}), 3)
        self.assertEqual(matching({"duration_ms": lt(300)}), 2)
        self.assertEqual(matching({"status_code": ne(200)}), 3)

    def test_filter_none_means_is_null(self):
        """A None value selects the rows where the column is NULL."""
        rows = self.table.aggregate({"user_id": None}, functions={"n": COUNT})
        self.assertEqual(rows[0]["n"], 1)

    def test_filter_exact_match(self):
        """A plain value is an exact match."""
        rows = self.table.aggregate({"route": "/api/traces"}, functions={"n": COUNT})
        self.assertEqual(rows[0]["n"], 5)

    def test_filters_agree_with_get_matching(self):
        """A filter selects the same rows get_matching returns, and aggregate() counts."""
        for query in ({"route": "/api/traces", "status_code": 500}, {"user_id": "u1"},
                      {"user_id": None}, {"duration_ms": between(200, 800)}, {"status_code": ne(200)}):
            with self.subTest(query=query):
                matching = self.table.get_matching(query)
                self.assertGreaterEqual(len(matching), 1)
                self.assertEqual(self.table.aggregate(query, functions={"n": COUNT})[0]["n"], len(matching))

    def test_filter_combines_with_grouping_and_buckets(self):
        """Filters narrow the rows before grouping and bucketing."""
        rows = self.table.aggregate({"status_code": 200}, group_by=["route"], functions={"n": COUNT},
                                    order_by=["route"])
        self.assertEqual([(row["route"], row["n"]) for row in rows],
                         [("/api/launch", 5), ("/api/traces", 2)])
        self.assertEqual(
            self.buckets(TimeBucket("started_at", "day"), {"route": "/api/traces"}),
            [("2026-10-31T00:00:00+00:00", 1), ("2026-11-01T00:00:00+00:00", 1),
             ("2026-11-02T00:00:00+00:00", 2), ("2026-11-03T00:00:00+00:00", 1)])

    # ---- Buckets -------------------------------------------------------------

    def test_bucket_minute(self):
        """A minute bucket keeps the minute and is reported with seconds and offset."""
        self.assertEqual(self.buckets(TimeBucket("started_at", "minute")), MINUTE_BUCKETS)

    def test_bucket_hour(self):
        """An hour bucket resets minutes and seconds."""
        rows = self.table.aggregate(
            {"started_at": gte("2026-11-02T00:00:00+00:00")},
            functions={"n": COUNT}, bucket=TimeBucket("started_at", "hour"))
        self.assertEqual([(row["bucket"], row["n"]) for row in rows], [
            ("2026-11-02T00:00:00+00:00", 1),
            ("2026-11-02T10:00:00+00:00", 3),
            ("2026-11-03T10:00:00+00:00", 1)])

    def test_bucket_day_with_a_custom_alias(self):
        """The bucket is reported under its alias, "bucket" by default."""
        bucket = TimeBucket("started_at", "day", alias="day")
        self.assertEqual(self.buckets(bucket), [
            ("2025-12-31T00:00:00+00:00", 1), ("2026-01-01T00:00:00+00:00", 1),
            ("2026-10-31T00:00:00+00:00", 1), ("2026-11-01T00:00:00+00:00", 2),
            ("2026-11-02T00:00:00+00:00", 4), ("2026-11-03T00:00:00+00:00", 1)])
        rows = self.table.aggregate(functions={"n": COUNT}, bucket=bucket, limit=1)
        self.assertEqual(list(rows[0]), ["day", "n"])

    def test_bucket_week_starts_on_monday(self):
        """A week starts on Monday 00:00 UTC: Sunday 2026-11-01 is still in October's week."""
        self.assertEqual(self.buckets(TimeBucket("started_at", "week")), [
            ("2025-12-29T00:00:00+00:00", 2),
            ("2026-10-26T00:00:00+00:00", 3),
            ("2026-11-02T00:00:00+00:00", 5)])

    def test_bucket_week_across_the_week_boundary(self):
        """Sunday 23:59:59 and Monday 00:00:00 fall in different weeks."""
        rows = self.table.aggregate(
            {"started_at": between("2026-11-01T00:00:00+00:00", "2026-11-02T23:59:59+00:00")},
            functions={"n": COUNT}, bucket=TimeBucket("started_at", "week"))
        self.assertEqual([(row["bucket"], row["n"]) for row in rows],
                         [("2026-10-26T00:00:00+00:00", 2), ("2026-11-02T00:00:00+00:00", 4)])

    def test_bucket_week_across_the_year_boundary(self):
        """New Year's Eve (Thursday) and New Year's Day (Friday) share a week."""
        rows = self.table.aggregate(
            {"started_at": lt("2026-01-02T00:00:00+00:00")},
            functions={"n": COUNT}, bucket=TimeBucket("started_at", "week"))
        self.assertEqual([(row["bucket"], row["n"]) for row in rows], [("2025-12-29T00:00:00+00:00", 2)])

    def test_bucket_month(self):
        """A month bucket starts on the first day of the month."""
        self.assertEqual(self.buckets(TimeBucket("started_at", "month")), [
            ("2025-12-01T00:00:00+00:00", 1), ("2026-01-01T00:00:00+00:00", 1),
            ("2026-10-01T00:00:00+00:00", 1), ("2026-11-01T00:00:00+00:00", 7)])

    def test_bucket_month_across_the_month_boundary(self):
        """2026-10-31T23:59:59 and 2026-11-01T00:00:00 fall in different months."""
        rows = self.table.aggregate(
            {"started_at": between("2026-10-31T23:59:59+00:00", "2026-11-01T00:00:00+00:00")},
            functions={"n": COUNT}, bucket=TimeBucket("started_at", "month"), order_by=["bucket"])
        self.assertEqual([(row["bucket"], row["n"]) for row in rows],
                         [("2026-10-01T00:00:00+00:00", 1), ("2026-11-01T00:00:00+00:00", 1)])

    def test_bucketed_with_no_matching_rows_is_empty(self):
        """No match with a bucket: no buckets, so no rows."""
        self.assertEqual(self.buckets(TimeBucket("started_at", "day"), {"route": "/api/none"}), [])

    def test_bucket_rows_are_ordered_by_bucket_by_default(self):
        """With a bucket, rows come back ordered by bucket ascending."""
        bucket = TimeBucket("started_at", "day")
        found = [row["bucket"] for row in self.table.aggregate(functions={"n": COUNT}, bucket=bucket)]
        self.assertEqual(found, sorted(found))
        self.assertEqual(found[0], "2025-12-31T00:00:00+00:00")

    def test_bucket_with_group_by(self):
        """A bucket is a grouping like any other, and sorts before the groups."""
        rows = self.table.aggregate(functions={"n": COUNT}, group_by=["route"],
                                    bucket=TimeBucket("started_at", "day"), order_by=["bucket", "route"])
        self.assertEqual([(row["bucket"], row["route"], row["n"]) for row in rows], [
            ("2025-12-31T00:00:00+00:00", "/api/launch", 1),
            ("2026-01-01T00:00:00+00:00", "/api/launch", 1),
            ("2026-10-31T00:00:00+00:00", "/api/traces", 1),
            ("2026-11-01T00:00:00+00:00", "/api/launch", 1),
            ("2026-11-01T00:00:00+00:00", "/api/traces", 1),
            ("2026-11-02T00:00:00+00:00", "/api/launch", 2),
            ("2026-11-02T00:00:00+00:00", "/api/traces", 2),
            ("2026-11-03T00:00:00+00:00", "/api/traces", 1)])

    def test_bucket_is_the_iso_8601_utc_second(self):
        """A bucket is an ISO 8601 string at second precision with a UTC offset."""
        buckets = [row["bucket"] for row in
                   self.table.aggregate(functions={"n": COUNT}, bucket=TimeBucket("started_at", "minute"))]
        self.assertEqual(buckets[0], "2025-12-31T23:59:00+00:00")
        for bucket in buckets:
            self.assertRegex(bucket, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")
            self.assertEqual(datetime.fromisoformat(bucket).utcoffset(), timedelta(0))

    def _untimed_table(self):
        """Create a sibling table whose timestamp column allows NULL, with three rows.

        Two rows are a minute apart; the third has no timestamp.
        """
        cls = type(self)
        table = cls.make_table(f"{cls.TABLE}_untimed")
        cls.drop_table(f"{cls.TABLE}_untimed")
        table.init_from_schema(
            f'CREATE TABLE IF NOT EXISTS "{cls.TABLE}_untimed" ('
            '"id" TEXT PRIMARY KEY, '
            f'"started_at" {cls.STARTED_AT_DDL.replace(" NOT NULL", "")}, '
            '"duration_ms" INTEGER);')
        table.insert_many([
            {"id": "a", "started_at": cls.started_at("2026-11-02T10:17:30+00:00"), "duration_ms": 100},
            {"id": "b", "started_at": cls.started_at("2026-11-02T10:18:30+00:00"), "duration_ms": 300},
            {"id": "c", "started_at": None, "duration_ms": 200},
        ])
        self.addCleanup(cls.drop_table, f"{cls.TABLE}_untimed")
        return table

    def test_rows_without_a_timestamp_form_the_last_bucket(self):
        """A NULL timestamp is a NULL bucket, and sorts after every real one."""
        table = self._untimed_table()
        functions = {"n": COUNT, "p50": Agg("percentile", "duration_ms", 0.5)}
        bucket = TimeBucket("started_at", "minute")
        rows = table.aggregate(functions=functions, bucket=bucket)
        self.assertEqual([(row["bucket"], row["n"], row["p50"]) for row in rows],
                         [("2026-11-02T10:17:00+00:00", 1, 100.0),
                          ("2026-11-02T10:18:00+00:00", 1, 300.0),
                          (None, 1, 200.0)])
        rows = table.aggregate(functions=functions, bucket=bucket, order_by=["-bucket"])
        self.assertEqual([row["bucket"] for row in rows],
                         [None, "2026-11-02T10:18:00+00:00", "2026-11-02T10:17:00+00:00"])

    # ---- Ordering and paging -------------------------------------------------

    def test_order_by_alias_ascending(self):
        """order_by on a function alias sorts by the computed value."""
        rows = self.table.aggregate(group_by=["status_code"], functions={"n": COUNT}, order_by=["n"])
        self.assertEqual([(row["status_code"], row["n"]) for row in rows],
                         [(404, 1), (500, 2), (200, 7)])

    def test_order_by_alias_descending(self):
        """A leading "-" sorts descending."""
        rows = self.table.aggregate(group_by=["status_code"], functions={"n": COUNT}, order_by=["-n"])
        self.assertEqual([(row["status_code"], row["n"]) for row in rows],
                         [(200, 7), (500, 2), (404, 1)])

    def test_order_by_bucket_descending(self):
        """order_by on the bucket alias overrides the default ascending order."""
        bucket = TimeBucket("started_at", "minute")
        self.assertEqual(self.buckets(bucket, order_by=["-bucket"]), MINUTE_BUCKETS[::-1])

    def test_order_by_several_names(self):
        """Later names break ties between earlier ones."""
        rows = self.table.aggregate(group_by=["route", "status_code"], functions={"n": COUNT},
                                    order_by=["-n", "route", "-status_code"])
        self.assertEqual([(row["route"], row["status_code"], row["n"]) for row in rows], [
            ("/api/launch", 200, 5),
            ("/api/traces", 500, 2),
            ("/api/traces", 200, 2),
            ("/api/traces", 404, 1)])

    def test_order_by_puts_null_last_ascending_and_first_descending(self):
        """A NULL group sorts after every value ascending, and before them descending."""
        functions = {"n": COUNT}
        rows = self.table.aggregate(group_by=["user_id"], functions=functions, order_by=["user_id"])
        self.assertEqual([(row["user_id"], row["n"]) for row in rows], [("u1", 5), ("u2", 4), (None, 1)])
        rows = self.table.aggregate(group_by=["user_id"], functions=functions, order_by=["-user_id"])
        self.assertEqual([(row["user_id"], row["n"]) for row in rows], [(None, 1), ("u2", 4), ("u1", 5)])

    def test_limit(self):
        """limit keeps the first rows."""
        rows = self.table.aggregate(group_by=["status_code"], functions={"n": COUNT},
                                    order_by=["-n"], limit=2)
        self.assertEqual([(row["status_code"], row["n"]) for row in rows], [(200, 7), (500, 2)])

    def test_offset(self):
        """offset skips the first rows, with or without a limit."""
        arguments = {'group_by': ["status_code"], 'functions': {"n": COUNT}, 'order_by': ["-n"]}
        rows = self.table.aggregate(offset=1, **arguments)
        self.assertEqual([(row["status_code"], row["n"]) for row in rows], [(500, 2), (404, 1)])
        rows = self.table.aggregate(offset=1, limit=5, **arguments)
        self.assertEqual([(row["status_code"], row["n"]) for row in rows], [(500, 2), (404, 1)])

    def test_limit_and_offset(self):
        """limit and offset page through the rows."""
        rows = self.table.aggregate(group_by=["status_code"], functions={"n": COUNT},
                                    order_by=["-n"], limit=1, offset=1)
        self.assertEqual([(row["status_code"], row["n"]) for row in rows], [(500, 2)])

    def test_limit_zero_and_offset_past_the_end(self):
        """Nothing is left to return."""
        arguments = {'group_by': ["status_code"], 'functions': {"n": COUNT}, 'order_by': ["-n"]}
        self.assertEqual(self.table.aggregate(limit=0, **arguments), [])
        self.assertEqual(self.table.aggregate(limit=5, offset=3, **arguments), [])

    def test_limit_and_offset_on_buckets(self):
        """limit and offset page through buckets."""
        bucket = TimeBucket("started_at", "minute")
        self.assertEqual(self.buckets(bucket, limit=3, offset=2), MINUTE_BUCKETS[2:5])
        self.assertEqual(self.buckets(bucket, offset=7), MINUTE_BUCKETS[7:])

    # ---- Validation ----------------------------------------------------------

    def test_invalid_arguments_raise_value_error(self):
        """Each invalid argument raises ValueError, naming what is wrong."""
        for what, message, arguments in INVALID:
            with self.subTest(what=what), self.assertRaisesRegex(ValueError, message):
                self.table.aggregate(**arguments)

    def test_identifiers_cannot_carry_sql(self):
        """No name that is not a plain identifier reaches the statement."""
        for name in BAD_NAMES:
            with self.subTest(name=name):
                calls = [
                    {"functions": {"n": COUNT}, "group_by": [name]},
                    {"functions": {name: COUNT}},
                    {"functions": {"s": Agg("sum", name)}},
                    {"query": {name: "x"}, "functions": {"n": COUNT}},
                    {"functions": {"n": COUNT}, "bucket": TimeBucket(name, "day")},
                    {"functions": {"n": COUNT}, "bucket": TimeBucket("started_at", "day", alias=name)},
                ]
                for arguments in calls:
                    with self.assertRaisesRegex(ValueError, "is not a valid name"):
                        self.table.aggregate(**arguments)

    def test_validation_precedes_database_work(self):
        """Nothing invalid gets as far as opening a connection."""
        touched = mock.Mock(side_effect=AssertionError("the database was reached before validation"))
        with mock.patch.object(type(self.table), self.CONNECTION_METHOD, touched):
            for what, _message, arguments in INVALID:
                with self.subTest(what=what), self.assertRaises(ValueError):
                    self.table.aggregate(**arguments)
            for name in BAD_NAMES:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    self.table.aggregate(functions={name: COUNT})
        touched.assert_not_called()


class TestSQLiteAggregate(AggregateCases, unittest.TestCase):
    """aggregate() on SQLiteTable."""

    CONNECTION_METHOD = "get_connection"

    @classmethod
    def make_table(cls, name):
        """Return a SQLiteTable, on the test database the harness selects."""
        from campus.storage.tables.backend.sqlite import SQLiteTable
        return SQLiteTable(name)

    @classmethod
    def drop_table(cls, name):
        """Remove the table from the test database."""
        from campus.storage.tables.backend.sqlite import SQLiteTable
        SQLiteTable(name).init_from_schema(f'DROP TABLE IF EXISTS "{name}"')


class PostgresFixture:
    """Runs AggregateCases on PostgreSQLTable against CAMPUS_TEST_POSTGRES_URI.

    Not a TestCase: mix it in ahead of AggregateCases. Without the
    variable every case is skipped from setUp(), so the count of cases
    that did not run stays visible in the test summary.
    """

    TABLE = "test_aggregate_spans"
    CONNECTION_METHOD = "_get_connection"
    # A PGTZ value makes every connection open with that session time zone.
    SESSION_TIME_ZONE = None
    uri = None

    @classmethod
    def setUpClass(cls):
        """Point the backend at the test database, then let the cases set up."""
        cls.uri = os.environ.get(POSTGRES_URI_VAR)
        if not cls.uri:
            return
        settings = {"POSTGRESDB_URI": cls.uri}
        if cls.SESSION_TIME_ZONE:
            settings["PGTZ"] = cls.SESSION_TIME_ZONE
        patcher = mock.patch.dict(os.environ, settings)
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        """Let the cases drop their table when they had set one up."""
        if cls.uri:
            super().tearDownClass()

    def setUp(self):
        """Skip every case until a test database is named."""
        if not self.uri:
            self.skipTest(SKIP_POSTGRES)

    @classmethod
    def make_table(cls, name):
        """Return a PostgreSQLTable; the backend reads POSTGRESDB_URI."""
        from campus.storage.tables.backend.postgres import PostgreSQLTable
        return PostgreSQLTable(name)

    @classmethod
    def drop_table(cls, name):
        """Remove the table from the test database."""
        import psycopg2
        with closing(psycopg2.connect(cls.uri)) as conn, conn.cursor() as cursor:
            cursor.execute(f'DROP TABLE IF EXISTS "{name}"')
            conn.commit()

    def test_driver_error_is_a_sanitized_storage_error(self):
        """An unknown column raises StorageError that carries no driver text (campus-core#8)."""
        with self.assertLogs("campus.storage.errors", level="ERROR"), \
                self.assertRaises(StorageError) as caught:
            self.table.aggregate(functions={"s": Agg("sum", "no_such_column")})
        message = str(caught.exception)
        self.assertIn("aggregate rows matching query", message)
        self.assertIn(self.TABLE, message)
        self.assertNotIn("no_such_column", message)
        self.assertNotIn("SELECT", message)

    def test_session_time_zone_does_not_change_the_result(self):
        """The result is the same whatever time zone the server session starts in."""
        arguments = {'functions': ALL_FUNCTIONS, 'bucket': TimeBucket("started_at", "minute")}
        expected = self.table.aggregate(**arguments)
        for zone in ("Asia/Singapore", "America/Los_Angeles"):
            with self.subTest(zone=zone), mock.patch.dict(os.environ, {"PGTZ": zone}):
                self.assertEqual(self.table.aggregate(**arguments), expected)


class TestPostgresAggregate(PostgresFixture, AggregateCases, unittest.TestCase):
    """aggregate() on PostgreSQLTable, timestamps stored as ISO 8601 text."""


class TestPostgresAggregateTimestamptz(PostgresFixture, AggregateCases, unittest.TestCase):
    """The same cases on a native TIMESTAMPTZ column, written at a +05:30 offset."""

    TABLE = "test_aggregate_spans_tz"
    STARTED_AT_DDL = "TIMESTAMPTZ NOT NULL"
    SESSION_TIME_ZONE = "America/New_York"

    @staticmethod
    def started_at(value):
        """The same instant, written in IST: an offset must not move the bucket."""
        return datetime.fromisoformat(value).astimezone(
            timezone(timedelta(hours=5, minutes=30))).isoformat()

    def test_fixture_runs_in_the_configured_time_zone(self):
        """Guard the premise of the class: the session is not in UTC."""
        import psycopg2
        with closing(psycopg2.connect(self.uri)) as conn, conn.cursor() as cursor:
            cursor.execute("SHOW timezone")
            self.assertEqual(cursor.fetchone()[0], self.SESSION_TIME_ZONE)


class TestPostgresAggregateTimestamp(PostgresFixture, AggregateCases, unittest.TestCase):
    """The same cases on a native TIMESTAMP column (no zone), in a non-UTC session."""

    TABLE = "test_aggregate_spans_naive"
    STARTED_AT_DDL = "TIMESTAMP NOT NULL"
    SESSION_TIME_ZONE = "Asia/Singapore"

    @staticmethod
    def started_at(value):
        """UTC wall time with no offset: the column cannot say it is UTC."""
        return value.removesuffix("+00:00")

    def test_fixture_runs_in_the_configured_time_zone(self):
        """Guard the premise of the class: the session is not in UTC."""
        import psycopg2
        with closing(psycopg2.connect(self.uri)) as conn, conn.cursor() as cursor:
            cursor.execute("SHOW timezone")
            self.assertEqual(cursor.fetchone()[0], self.SESSION_TIME_ZONE)


class TestAggregationTypes(unittest.TestCase):
    """The Agg and TimeBucket value objects."""

    def test_are_named_tuples_with_defaults(self):
        """The types unpack as tuples, so they can be built from stored configuration."""
        self.assertEqual(Agg("count"), ("count", None, None))
        self.assertEqual(Agg("percentile", "duration_ms", 0.99)[:], ("percentile", "duration_ms", 0.99))
        self.assertEqual(TimeBucket("started_at", "hour"), ("started_at", "hour", "bucket"))
        self.assertEqual(TimeBucket("started_at", "day", alias="day").alias, "day")

    def test_are_exported_from_campus_storage(self):
        """They are public the way the query operators are."""
        import campus.storage
        from campus.storage import query
        for name in ("Agg", "TimeBucket"):
            self.assertIn(name, campus.storage.__all__)
            self.assertIs(getattr(campus.storage, name), getattr(query, name))

    def test_aggregate_is_part_of_the_table_interface(self):
        """TableInterface declares it; both table backends define it."""
        from campus.storage import TableInterface
        from campus.storage.tables.backend.postgres import PostgreSQLTable
        from campus.storage.tables.backend.sqlite import SQLiteTable
        self.assertIn("aggregate", vars(TableInterface))
        for backend in (PostgreSQLTable, SQLiteTable):
            self.assertTrue(issubclass(backend, TableInterface))
            self.assertIn("aggregate", vars(backend))

    def test_a_subclass_written_before_aggregate_still_instantiates(self):
        """A patch release is additive: the new method is not abstract, so
        a subclass that predates it (a consumer's test double) still
        instantiates, and calling the method names what is missing."""
        from campus.storage import TableInterface

        class PredatesAggregate(TableInterface):
            def get_by_id(self, row_id):
                pass

            def get_matching(self, query, **kwargs):
                pass

            def insert_one(self, row):
                pass

            def update_by_id(self, row_id, update):
                pass

            def update_matching(self, query, update):
                pass

            def delete_by_id(self, row_id):
                pass

            def delete_matching(self, query):
                pass

            def init_from_model(self, name, model):
                pass

            def init_from_schema(self, schema):
                pass

        table = PredatesAggregate("t")
        with self.assertRaises(NotImplementedError) as raised:
            table.aggregate(functions={"n": COUNT})
        self.assertIn("PredatesAggregate", str(raised.exception))


class TestPercentile(unittest.TestCase):
    """The continuous percentile, which the SQLite backend computes in Python."""

    def _percentile(self, values, fraction):
        from campus.storage.tables.backend import _aggregate
        return _aggregate.percentile(values, fraction)

    def test_agrees_with_statistics_quantiles(self):
        """It is the "inclusive" method of statistics.quantiles: linear interpolation."""
        for values in ([15, 20, 35, 40, 50], list(range(1, 101)), [3.5, -2, 0, 11.25, 7], [2, 2, 9]):
            expected = statistics.quantiles(values, n=4, method="inclusive")
            for fraction, cut_point in zip((0.25, 0.5, 0.75), expected, strict=True):
                with self.subTest(values=values, fraction=fraction):
                    self.assertEqual(self._percentile(values, fraction), cut_point)

    def test_definition_by_rank(self):
        """Rank = fraction * (n - 1), interpolated between the neighbouring values."""
        self.assertEqual(self._percentile([30, 10, 20], 0.5), 20.0)
        self.assertEqual(self._percentile([10, 20, 30], 0), 10.0)
        self.assertEqual(self._percentile([10, 20, 30], 1), 30.0)
        self.assertEqual(self._percentile([10, 20, 30, 40], 0.5), 25.0)
        self.assertEqual(self._percentile([10, 20, 30, 40], 0.75), 32.5)
        self.assertEqual(self._percentile([10], 0.99), 10.0)
        self.assertIsNone(self._percentile([], 0.5))

    def test_sqlite_aggregate_skips_nulls_and_returns_none_when_empty(self):
        """The campus_percentile aggregate ignores NULL and yields NULL over no value."""
        from campus.storage.tables.backend import _aggregate
        connection = sqlite3.connect(":memory:")
        connection.create_aggregate("campus_percentile", 2, _aggregate.Percentile)
        values = "SELECT 30 AS x UNION ALL SELECT NULL UNION ALL SELECT 10 UNION ALL SELECT 20"
        found = connection.execute(f"SELECT campus_percentile(0.5, x) FROM ({values})").fetchone()[0]
        self.assertEqual(found, 20.0)
        found = connection.execute(
            "SELECT campus_percentile(0.5, x) FROM (SELECT NULL AS x UNION ALL SELECT NULL)").fetchone()[0]
        self.assertIsNone(found)
        found = connection.execute("SELECT campus_percentile(0.5, x) FROM (SELECT 1 AS x WHERE 0)").fetchone()[0]
        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
