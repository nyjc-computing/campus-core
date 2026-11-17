"""Tests for storage-driver error sanitization (campus-core#8).

Driver exception texts embed internals — pymongo's InvalidURI echoes
the credential-bearing connection URI, psycopg2 IntegrityError carries
the failing SQL statement, boto ClientError carries endpoints and
request ids. The sanitizer keeps those out of StorageError
messages/details (which fold into str()) and out of student-visible
responses; the driver detail goes to the server log once, with
credential-bearing URI fragments masked.
"""

import unittest
from unittest import mock

from campus.common import env

env.set('STORAGE_MODE', "1")

from campus.storage.errors import (  # noqa: E402
    ConflictError,
    StorageError,
    sanitize_driver_error,
)


class FakeUriError(Exception):
    """Driver-style exception carrying a credential-bearing URI."""
    pass


class TestSanitizeDriverError(unittest.TestCase):

    def test_message_carries_class_and_operation_only(self):
        err = sanitize_driver_error(
            FakeUriError("mongodb://user:hunter2@db.internal:27017/?authSource=admin"),
            operation="retrieve document by id",
            group_name="users",
        )
        self.assertIsInstance(err, StorageError)
        self.assertIn("FakeUriError", str(err))
        self.assertIn("retrieve document by id", str(err))
        self.assertIn("users", str(err))
        self.assertNotIn("hunter2", str(err))
        self.assertNotIn("db.internal", str(err))

    def test_error_cls_passthrough(self):
        err = sanitize_driver_error(
            ValueError("boom"),
            operation="update row",
            error_cls=ConflictError,
        )
        self.assertIsInstance(err, ConflictError)

    def test_cause_chain_is_not_attached(self):
        """Call sites suppress the cause: driver text must not re-enter
        logs via a chained traceback (call sites use ``from None``; the
        helper itself sets nothing)."""
        original = FakeUriError("secret")

        def raise_site():
            try:
                raise original
            except FakeUriError:
                raise sanitize_driver_error(
                    original, operation="op",
                ) from None

        with self.assertRaises(StorageError) as ctx:
            raise_site()
        self.assertIsNone(ctx.exception.__cause__)
        self.assertNotIn("secret", str(ctx.exception))

    def test_log_line_masks_credential_uri(self):
        with self.assertLogs("campus.storage.errors", level="ERROR") as captured:
            sanitize_driver_error(
                FakeUriError("InvalidURI: mongodb://user:hunter2@db.internal:27017"),
                operation="connect",
            )
        joined = "\n".join(captured.output)
        self.assertIn("FakeUriError", joined)
        self.assertIn("://user:***@", joined)
        self.assertNotIn("hunter2", joined)

    def test_log_line_keeps_non_uri_driver_text(self):
        """Non-URI driver detail stays in the (superadmin-only) log."""
        with self.assertLogs("campus.storage.errors", level="ERROR") as captured:
            sanitize_driver_error(
                FakeUriError("IntegrityError: duplicate key value violates "
                             "unique constraint users_pkey"),
                operation="insert row",
                group_name="users",
            )
        joined = "\n".join(captured.output)
        self.assertIn("duplicate key value violates", joined)


class TestMongoBackendSanitization(unittest.TestCase):
    """Backend catch-alls raise sanitized errors, not driver text."""

    def test_get_by_id_sanitizes_driver_failure(self):
        from campus.storage.documents.backend.mongodb import MongoDBCollection

        backend = MongoDBCollection("users")
        driver_error = FakeUriError(
            "pymongo.errors.InvalidURI: mongodb://user:hunter2@db.internal"
        )
        collection = mock.Mock()
        collection.find_one.side_effect = driver_error
        with mock.patch.object(
                MongoDBCollection, "collection",
                new_callable=mock.PropertyMock, return_value=collection,
        ), self.assertRaises(StorageError) as ctx, self.assertLogs(
                "campus.storage.documents.backend.mongodb", level="ERROR"):
            backend.get_by_id("doc1")
        self.assertNotIn("hunter2", str(ctx.exception))
        self.assertNotIn("db.internal", str(ctx.exception))
        self.assertIn("retrieve document by id", str(ctx.exception))


class TestConflictDetailsScrub(unittest.TestCase):

    def test_insert_conflict_details_carry_id_only(self):
        """Duplicate-key conflicts expose the doc id, not the full row
        (rows may carry secrets) and not driver text."""
        from campus.storage.documents.backend.mongodb import (
            MongoDBCollection,
            MongoRecord,
        )

        backend = MongoDBCollection("users")
        row = MongoRecord({"id": "doc1", "name": "Jun", "otp": "123456"})

        class DuplicateKeyLike(Exception):
            pass

        collection = mock.Mock()
        collection.insert_one.side_effect = DuplicateKeyLike(
            "E11000 duplicate key error collection users index _id_ dup"
        )
        # The real code imports DuplicateKeyError from pymongo; make the
        # message-based fallback path take the conflict branch.
        with mock.patch(
                "campus.storage.documents.backend.mongodb.DuplicateKeyError",
                DuplicateKeyLike,
        ), mock.patch.object(
            MongoDBCollection, "collection",
            new_callable=mock.PropertyMock, return_value=collection,
        ), self.assertRaises(ConflictError) as ctx:
            backend.insert_one(dict(row))
        self.assertEqual(ctx.exception.details, {"id": "doc1"})
        self.assertNotIn("Jun", str(ctx.exception))
        self.assertNotIn("123456", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
