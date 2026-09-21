"""Release safety tests: no certificate, network, signing or publishing operations."""

import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "release", Path(__file__).resolve().parents[1] / "mac" / "release.py"
)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_identity_must_be_an_available_developer_id(self):
        with self.assertRaises(ValueError):
            release.validate_identity("-", "0 valid identities found")
        with self.assertRaises(ValueError):
            release.validate_identity(
                "Developer ID Application: Test (TEST)", "0 valid identities found"
            )
        release.validate_identity(
            "Developer ID Application: Test (TEST)",
            '1) ABCD "Developer ID Application: Test (TEST)"',
        )

    def test_notarization_rejection_cannot_create_release(self):
        for status in (None, "Invalid", "In Progress", "Rejected"):
            with self.subTest(status=status), self.assertRaises(RuntimeError):
                release.require_accepted({"status": status, "id": "test"})

    def test_accepted_notarization_can_continue(self):
        release.require_accepted({"status": "Accepted"})
