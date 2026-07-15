import unittest

from utils.log import Logger


class LoggerSanitizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = Logger("chatgpt2api-test-log-sanitization")

    def test_sensitive_image_debug_payload_is_safe_to_log(self) -> None:
        token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature"
        signed_url = (
            "https://chatgpt.com/backend-api/estuary/content?"
            "id=file_example&sig=super-secret-signature&v=0"
        )
        sanitized = self.logger._sanitize(
            {
                "token_prefix": token,
                "account_email": "person@example.com",
                "urls": [signed_url],
                "message": f"Bearer {token}; result={signed_url}; owner=person@example.com",
            }
        )

        rendered = str(sanitized)
        self.assertNotIn(token, rendered)
        self.assertNotIn("super-secret-signature", rendered)
        self.assertNotIn("person@example.com", rendered)
        self.assertEqual(sanitized["account_email"], "[redacted-email]")
        self.assertEqual(sanitized["urls"], ["https://chatgpt.com/backend-api/estuary/content"])
        self.assertIn("sha256=", sanitized["token_prefix"])

    def test_plain_non_sensitive_fields_remain_available(self) -> None:
        sanitized = self.logger._sanitize({"event": "image_poll_start", "attempt": 2, "host": "chatgpt.com"})
        self.assertEqual(sanitized, {"event": "image_poll_start", "attempt": 2, "host": "chatgpt.com"})


if __name__ == "__main__":
    unittest.main()
