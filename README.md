# campus-core

Campus storage, web authentication, and shared services, shipped as a
PEP 420 namespace portion under `campus/` — `campus.storage` (tables,
documents, objects + query operators), `campus.webauth` (HTTP auth
schemes, OAuth2 client flows), and `campus.services.email`.

Part of the Campus distribution split
([nyjc-computing/campus#970](https://github.com/nyjc-computing/campus/issues/970)):
campus-core is consumed **only** by campus-suite — downstream apps
install campus-common + campus-model + flask-campus and never touch
storage/webauth. Database drivers are extras, and the backends import
them lazily: importing this package loads no driver at all.

## Install

Release wheels are consumed as version-pinned URLs (Poetry):

```toml
campus-core = {url = "https://github.com/nyjc-computing/campus-core/releases/download/v0.1.0/campus_core-0.1.0-py3-none-any.whl"}
```

campus-suite declares all three driver extras. Extras for standalone
use:

```toml
campus-core = {url = "…", extras = ["postgres", "mongo", "s3"]}
```

| Extra | Driver | Backend |
|---|---|---|
| `postgres` | psycopg2-binary | tables |
| `mongo` | pymongo[srv] | documents |
| `s3` | boto3 | objects (S3-compatible) |

The sqlite and in-memory backends need no extra.

Dependencies: `requests` (webauth's OAuth2 flows) and the
[campus-common](https://github.com/nyjc-computing/campus-common) /
[campus-model](https://github.com/nyjc-computing/campus-model) wheels.

## Public interface (`__all__` per package)

- **campus.storage** — `get_table` / `get_collection` / `get_bucket`
  facades, the `TableInterface` / `CollectionInterface` /
  `BucketInterface` protocols, storage errors
  (`StorageError`/`NotFoundError`/`ConflictError`/…), `purge_*` helpers,
  the query operators (`gt`, `gte`, `lt`, `lte`, `ne`, `between`,
  `is_operator`), and the aggregation types `Agg` and `TimeBucket`
  (see [Aggregation](#aggregation)). Backends (sqlite, postgres, memory,
  mongodb, local, railway-s3) import their drivers lazily inside
  functions.
- **campus.webauth** — `http` (HTTP Basic/Bearer schemes) and `oauth2`
  (authorization-code, client-credentials, device-authorization flows).
- **campus.services.email** — `EmailSenderInterface` + `create_email_sender`
  (SMTP backend; drivers of other kinds plug in via the factory).

## Aggregation

`TableInterface.aggregate()` answers count / group-by / percentile /
time-bucket questions in one query. It takes the filters of
`get_matching()` and returns a list of dicts: one row of totals without
grouping — even when nothing matches — and one row per group otherwise.

```python
from campus.storage import Agg, TimeBucket, get_table, lt

spans = get_table("audit_spans")
rows = spans.aggregate(
    {"status_code": lt(500)},
    bucket=TimeBucket("started_at", "minute"),
    functions={
        "requests": Agg("count"),
        "p50_ms": Agg("percentile", "duration_ms", 0.5),
        "p99_ms": Agg("percentile", "duration_ms", 0.99),
    },
    order_by=["-requests"],
    limit=10,
)
# [{"bucket": "2026-11-24T10:17:00+00:00", "requests": 12, "p50_ms": 41.5, "p99_ms": 318.0}, ...]
```

- `functions` maps an alias to an `Agg`: `count` (rows, or the non-null
  values of a column), `count_distinct`, `sum`, `avg`, `min`, `max` and
  `percentile` (continuous, with linear interpolation between neighbouring
  values). Counts are ints; the rest are floats, or `None` where the
  column has no value.
- `group_by` lists columns to group by. `bucket` groups a timestamp
  column into `minute`, `hour`, `day`, `week` (from Monday 00:00 UTC) or
  `month` buckets, reported as ISO 8601 UTC strings such as
  `2026-11-24T10:17:00+00:00`.
- `order_by`, `limit` and `offset` sort and page the result. Rows are
  ordered only by `order_by`, or by bucket when there is one; a `NULL`
  sorts after every other value.
- An invalid argument raises `ValueError` before the database is
  touched.

Both table backends implement it with the same semantics. The postgres
backend lets the database do the work and reads timestamps as UTC
whatever the server's time zone, on ISO 8601 text and on native
`TIMESTAMPTZ` / `TIMESTAMP` columns alike. The sqlite backend computes
`percentile` in Python, so it keeps each group's values in memory.

## Boundary rules (enforced in this repo's tests)

- No service imports, ever: `campus.auth`, `campus.api`,
  `campus.audit`, `campus.yapper`, `campus.flask_campus`,
  `campus.deploy`, `campus.config` — services import core, never the
  reverse.
- No module-level flask/werkzeug anywhere in the dist.
- Driver laziness: importing the dist loads no database driver.

## Versioning

Strict 0.x: **patch** releases (`0.x.Y`) are additive or fix-only —
downstream flows them in at the next relock; **minor** releases
(`0.Y.0`) are the breaking line. Every interface change ships with a
CHANGELOG entry, a manifest regen
(`scripts/generate_interface_manifest.py`), and the version bump the
diff dictates. The interface manifest is enforced in CI.

## Development

```bash
poetry install
poetry run python -m unittest discover -s tests -t . -v
poetry run ruff check .
poetry run python scripts/generate_interface_manifest.py --check
```

The `aggregate()` tests run against SQLite by default. To also run them
against a live PostgreSQL, point `CAMPUS_TEST_POSTGRES_URI` at a
throwaway database (the tests create and drop tables there):

```bash
docker run -d --rm --name campus-core-agg-pg -e POSTGRES_PASSWORD=test \
    -e POSTGRES_DB=campus_test -p 55432:5432 postgres:16-alpine
export CAMPUS_TEST_POSTGRES_URI=postgresql://postgres:test@localhost:55432/campus_test
```

Releases: merge to `main` (ruleset-protected PRs), tag `vX.Y.Z`,
`poetry build`, attach `dist/campus_core-X.Y.Z-py3-none-any.whl` to
the GitHub release.
