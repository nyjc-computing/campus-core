# Changelog

## 0.1.12 — 2026-11-25

**Additive**: `TableInterface.aggregate()` — count / group-by /
percentile / time-bucket queries for campus.audit (campus#1243). Both
table backends implement it with the same semantics.

- `campus.storage` exports two new public types: `Agg` (a `func` of
  `count`, `count_distinct`, `sum`, `avg`, `min`, `max` or `percentile`,
  a `column`, and a `fraction` for `percentile`) and `TimeBucket` (a
  timestamp `column`, a `width` of `minute`/`hour`/`day`/`week`/`month`,
  and an `alias`).
- `aggregate(query, *, group_by, functions, bucket, order_by, limit,
  offset)` filters with the language of `get_matching` and returns a
  list of dicts: one row of totals without grouping (even when nothing
  matches), one row per group otherwise. Counts are ints; every other
  function is a float, or None where the column has no value. Buckets
  are ISO 8601 UTC strings at second precision, starting on UTC
  boundaries (weeks on Monday).
- `percentile` is continuous, with linear interpolation between
  neighbouring values: PostgreSQL's `percentile_cont`, which the sqlite
  backend computes in Python through its `campus_percentile` aggregate.
- An invalid argument raises `ValueError` before the database is
  touched. Identifiers only ever reach SQL validated and double-quoted,
  and every value is a bound parameter.
- PostgreSQL reads timestamps as UTC whatever the session time zone, so
  text without an offset and native `TIMESTAMP` columns bucket like
  `TIMESTAMPTZ`. Ordering states where NULL sorts, because the two
  engines' defaults differ (it sorts last ascending). The sqlite
  backend's `aggregate()` needs SQLite 3.30 or later for that syntax.
- `aggregate()` is abstract on `TableInterface`: a subclass defined
  outside this dist must implement it to stay instantiable.

## 0.1.8 — 2026-11-17

Pin-only rebuild: campus-model v0.2.2 → v0.2.3
(APIKey trace-read scoping binds, campus#944 / campus-model#13).
No code changes.

## 0.1.7 — 2026-11-17

Pin-only rebuild: campus-common v0.4.0 → v0.5.0
(from_exception static-message fix, campus#1128) + campus-model
v0.2.1 → v0.2.2 (rebuild of the same bump). No code changes.

## 0.1.6 — 2026-11-17

**Policy**: sanitize storage-driver error surfaces (campus-core#8;
environment-access-policy 2026-11-17 §3/§4) + pin rebuild.

- Storage backends no longer fold raw driver exception text into
  `StorageError` messages/details. Driver texts embed internals:
  pymongo's `InvalidURI` echoes the credential-bearing connection URI,
  psycopg2's `IntegrityError` carries the failing SQL statement, boto
  `ClientError` carries S3 endpoints and request ids — and
  `StorageError` folds `details` into its message, so raw driver text
  could reach server logs and (through the generic error handler)
  student-visible responses. All backend failure sites now raise
  sanitized errors (driver class + operation + collection/table only)
  via the new `campus.storage.errors.sanitize_driver_error`; the full
  driver text is logged server-side once, with credential-bearing URI
  fragments masked, and the cause chain is suppressed (`from None`) so
  downstream tracebacks can't re-leak it.
- Duplicate-key conflicts (`ConflictError` on documents/tables
  inserts/updates) no longer echo the full record/update dict in
  `details` — rows can carry secrets — only the document/row id.
- Pins: campus-common v0.3.1 → v0.4.0 (error-surface scrubs,
  campus-common#8), campus-model v0.2.0 → v0.2.1 (pin-only rebuild of
  the same bump).

## 0.1.5 — 2026-11-16

Dependency rebuild: campus-model pin v0.1.7 -> v0.2.0 (documents-backed
`Venue` reshape, campus-model#9 / campus#1099). No code changes —
campus-core imports only `campus.model.base`, which is unchanged; the
pin moves per the URL-pin collision rule so shared lockfiles with the
suite stay resolvable.

## 0.1.2 — 2026-11-07

Dependency rebuild: campus-model pin v0.1.4 -> v0.1.5 (`TimetableEntry`
label persistence, campus-model#5 / campus#1080). No code changes -
direct-URL deps bake the pinned wheel URL into this distribution's
metadata, so the bump requires a rebuild here per the pin-rebuild
contract.

## 0.1.1 — 2026-11-05

Dependency rebuild: campus-model pin v0.1.3 -> v0.1.4 (new
`HostedApp` model, campus-classroom#81 / campus#1066). No code
changes - direct-URL deps bake the pinned wheel URL into this
distribution's metadata, so the bump requires a rebuild here per
the pin-rebuild contract.

## 0.1.0 — 2026-11-04

Initial release: `campus.storage`, `campus.webauth`, and
`campus.services` carved verbatim from the campus monorepo (campus#992,
the final carve of the #970 distribution split). One PEP 420 namespace
portion under `campus/` holding the three packages.

- `campus.storage`: tables/documents/objects facades + backends
  (sqlite, postgres, memory, mongodb, local, railway-s3 — drivers are
  extras and stay lazily imported), query operators, storage errors.
- `campus.webauth`: HTTP Basic/Bearer schemes + OAuth2 client flows
  (authorization code, client credentials, device authorization).
- `campus.services.email`: the email sender interface + SMTP backend.

Interface kit from day one: closed-world `__all__` per package, checked-in
interface manifest, boundary guard (no service imports; no module-level
flask), and a driver-laziness runtime check; strict-0.x versioning
(README/CHANGELOG).

One carve addition: `campus/services/__init__.py` (the monorepo shipped
the directory init-less) — a minimal owning init declaring `email`, so
the dist's interface kit can manifest the package surface.
