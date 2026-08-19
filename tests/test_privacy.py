import unittest

from scaleverifier.privacy import redact_text


class PrivacyTests(unittest.TestCase):
    def test_redacts_common_tokens(self):
        value = "token=supersecretvalue123 and sk-abcdefghijklmnopqrstuvwxyz"
        redacted = redact_text(value)
        self.assertNotIn("supersecretvalue123", redacted)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", redacted)
        self.assertGreaterEqual(redacted.count("[REDACTED]"), 2)


if __name__ == "__main__":
    unittest.main()
