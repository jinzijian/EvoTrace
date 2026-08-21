import unittest

from evotrace.privacy import redact_for_cloud_text, redact_text


class PrivacyTests(unittest.TestCase):
    def test_redacts_common_tokens(self):
        value = "token=supersecretvalue123 and sk-abcdefghijklmnopqrstuvwxyz"
        redacted = redact_text(value)
        self.assertNotIn("supersecretvalue123", redacted)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", redacted)
        self.assertGreaterEqual(redacted.count("[REDACTED]"), 2)

    def test_cloud_redaction_removes_identity_and_host_details(self):
        value = (
            "/Users/alex/private /data01/team/repo C:\\Users\\alex\\repo "
            "owner@example.com 192.168.1.24 ssh build-user@private-host"
        )
        redacted = redact_for_cloud_text(value)
        self.assertNotIn("alex", redacted)
        self.assertNotIn("/data01", redacted)
        self.assertNotIn("private-host", redacted)
        self.assertNotIn("owner@example.com", redacted)
        self.assertNotIn("192.168.1.24", redacted)


if __name__ == "__main__":
    unittest.main()
