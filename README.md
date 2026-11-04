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
  and the query operators (`gt`, `gte`, `lt`, `lte`, `ne`, `between`,
  `is_operator`). Backends (sqlite, postgres, memory, mongodb, local,
  railway-s3) import their drivers lazily inside functions.
- **campus.webauth** — `http` (HTTP Basic/Bearer schemes) and `oauth2`
  (authorization-code, client-credentials, device-authorization flows).
- **campus.services.email** — `EmailSenderInterface` + `create_email_sender`
  (SMTP backend; drivers of other kinds plug in via the factory).

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

Releases: merge to `main` (ruleset-protected PRs), tag `vX.Y.Z`,
`poetry build`, attach `dist/campus_core-X.Y.Z-py3-none-any.whl` to
the GitHub release.
