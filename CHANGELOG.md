# Changelog

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
