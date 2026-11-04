"""Boundary rules for the campus-core dist (campus#992, #970 contract).

campus-core owns the storage/webauth/services layers. It must never
import a service module (campus.auth / campus.api / campus.audit /
campus.yapper / campus.flask_campus / campus.deploy / campus.config):
services import core, never the reverse. Its only campus deps are
campus-common and campus-model.

Driver laziness: importing campus.storage must not load
psycopg2/pymongo/boto3 — the backends import their drivers inside
functions, so a consumer that never opens a postgres/mongo/s3-backed
table never needs the driver (they are extras; downstream apps never
install this dist at all).
"""

import ast
import pathlib
import subprocess
import sys
import unittest

import campus.services
import campus.storage
import campus.webauth

SRCS = [
    pathlib.Path(campus.storage.__file__).resolve().parent.parent,
]

FORBIDDEN_ROOTS = (
    "campus.auth",
    "campus.api",
    "campus.audit",
    "campus.yapper",
    "campus.flask_campus",
    "campus.deploy",
    "campus.config",
)

# Module-level flask/werkzeug is banned across the dist (backends never
# touch web frameworks; services.email speaks SMTP).
NO_MODULE_LEVEL = {"flask", "werkzeug"}


class TestCoreBoundaries(unittest.TestCase):
    """No service imports (module level or lazy); no module-level flask."""

    def test_no_service_imports(self):
        offenders: list[str] = []
        for src in SRCS:
            for py in src.rglob("*.py"):
                tree = ast.parse(py.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.level == 0:
                        names = [node.module or ""]
                    else:
                        continue
                    for name in names:
                        if any(name == f or name.startswith(f + ".") for f in FORBIDDEN_ROOTS):
                            offenders.append(f"{py.name}: {name}")
                        if name.split(".")[0] in NO_MODULE_LEVEL:
                            offenders.append(f"{py.name}: module-level {name}")
        self.assertEqual([], offenders)


class TestDriverLaziness(unittest.TestCase):
    """Importing the dist must not load any database/object driver."""

    def test_import_does_not_load_drivers(self):
        code = (
            "import sys\n"
            "import campus.storage, campus.webauth, campus.services.email\n"
            "banned = [m for m in ('psycopg2', 'psycopg2_binary', 'pymongo', 'boto3')"
            " if m in sys.modules]\n"
            "print('BANNED:' + ','.join(banned) if banned else 'CLEAN')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        )
        self.assertTrue(
            result.stdout.strip().startswith("CLEAN"),
            f"drivers loaded on import: {result.stdout.strip()}",
        )


if __name__ == "__main__":
    unittest.main()
