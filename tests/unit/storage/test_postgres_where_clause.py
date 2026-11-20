"""Test Postgres backend WHERE-clause operator handling (campus-core#3).

`between` was unimplemented in the Postgres `_build_where_clause`: an
unknown operator fell through to the exact-match branch, binding the
operator's tuple value as a single parameter and producing
`"started_at" = ('...', '...')` (text = record) at the database. The
sqlite backend already handles `between`; these tests pin the Postgres
builder to the same semantics. Pure builder tests — no live database.
"""

import unittest


class TestPostgresWhereClause(unittest.TestCase):
    """Test PostgreSQLTable._build_where_clause operator branches."""

    def _build(self, query):
        from campus.storage.tables.backend.postgres import PostgreSQLTable
        return PostgreSQLTable._build_where_clause(query)

    def test_between_operator(self):
        """between binds both bounds as separate parameters (#3)."""
        from campus.storage.query import between
        clause, params = self._build(
            {"started_at": between("2026-11-01T00:00:00Z", "2026-11-30T23:59:59Z")}
        )
        self.assertEqual(
            clause,
            'WHERE ("started_at" >= %s AND "started_at" <= %s)',
        )
        self.assertEqual(
            params,
            ["2026-11-01T00:00:00Z", "2026-11-30T23:59:59Z"],
        )

    def test_between_composes_with_exact_match(self):
        """between composes with exact-match conditions via AND."""
        from campus.storage.query import between
        clause, params = self._build(
            {
                "user_id": "user@example.com",
                "started_at": between("2026-11-01T00:00:00Z", "2026-11-30T23:59:59Z"),
            }
        )
        self.assertIn('"user_id" = %s', clause)
        self.assertIn('("started_at" >= %s AND "started_at" <= %s)', clause)
        self.assertIn(" AND ", clause)
        # Exact-match param precedes the between bounds (dict order)
        self.assertEqual(
            params,
            [
                "user@example.com",
                "2026-11-01T00:00:00Z",
                "2026-11-30T23:59:59Z",
            ],
        )

    def test_comparison_operators_unaffected(self):
        """gt/gte/lt/lte/ne branches behave as before the between addition."""
        from campus.storage.query import gt, gte, lt, lte, ne
        clause, params = self._build(
            {
                "count": gt(5),
                "floor": gte(1),
                "ceiling": lt(100),
                "cap": lte(99),
                "status": ne("error"),
            }
        )
        self.assertEqual(
            clause,
            'WHERE "count" > %s AND "floor" >= %s AND "ceiling" < %s'
            ' AND "cap" <= %s AND "status" != %s',
        )
        self.assertEqual(params, [5, 1, 100, 99, "error"])

    def test_exact_match_still_binds_value_directly(self):
        """Plain values bind as exact matches, not operator fallthroughs."""
        clause, params = self._build({"user_id": "user@example.com"})
        self.assertEqual(clause, 'WHERE "user_id" = %s')
        self.assertEqual(params, ["user@example.com"])


if __name__ == "__main__":
    unittest.main()
