import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("CHATGPT2API_AUTH_KEY", "chatgpt2api")

from services.register import dropmail_provider, mail_provider, random_mail_domain_health


class DropMailProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.health_file = Path(self.temp_dir.name) / "random_mail_domain_health.json"
        self.dropmail_token_file = Path(self.temp_dir.name) / "dropmail_token_cache.json"
        self.health_patch = patch.object(
            random_mail_domain_health,
            "RANDOM_MAIL_DOMAIN_HEALTH_FILE",
            self.health_file,
        )
        self.dropmail_token_patch = patch.object(
            dropmail_provider,
            "DROPMAIL_TOKEN_CACHE_FILE",
            self.dropmail_token_file,
        )
        self.health_patch.start()
        self.dropmail_token_patch.start()

    def tearDown(self):
        self.dropmail_token_patch.stop()
        self.health_patch.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def _dropmail(**entry_overrides):
        entry = {"provider_ref": "dropmail#1", **entry_overrides}
        return dropmail_provider.DropMailProvider(entry, mail_provider._config({}))

    def test_dropmail_queries_dynamic_domains_and_ignores_fixed_domain_field(self):
        rejected = {
            "provider": "dropmail",
            "provider_ref": "dropmail#1",
            "address": "old@blocked.test",
            "domain_family": "blocked.test",
            "random_domain": True,
        }
        mail_provider.mark_mailbox_result(rejected, success=False, error="unsupported_email")
        provider = self._dropmail(domain=["fixed.example"], random_domain_attempts=3)
        provider._generate_token = lambda: "dynamic-token"
        queries = []

        def fake_graphql(token, query, variables=None, **kwargs):
            queries.append((query, variables))
            if "domains" in query:
                return {
                    "domains": [
                        {"id": "blocked", "name": "blocked.test"},
                        {"id": "healthy", "name": "healthy.test"},
                    ]
                }
            self.assertEqual(variables["input"]["domainId"], "healthy")
            return {
                "introduceSession": {
                    "id": "session-1",
                    "addresses": [{"id": "address-1", "address": "random@healthy.test"}],
                }
            }

        provider._graphql = fake_graphql
        try:
            with patch.object(dropmail_provider.random, "shuffle", lambda values: None):
                mailbox = provider.create_mailbox("ignored")
        finally:
            provider.close()

        self.assertEqual(len(queries), 2)
        self.assertEqual(mailbox["address"], "random@healthy.test")
        self.assertEqual(mailbox["api_token"], "dynamic-token")
        self.assertEqual(mailbox["session_id"], "session-1")
        self.assertEqual(mailbox["domain_health_skipped"], ["blocked.test"])
        self.assertNotEqual(mailbox["domain"], "fixed.example")

    def test_job_exclusion_skips_a_previously_successful_domain(self):
        mail_provider.mark_mailbox_result(
            {
                "provider": "dropmail",
                "provider_ref": "dropmail#1",
                "address": "old@preferred.test",
                "domain_family": "preferred.test",
                "random_domain": True,
            },
            success=True,
        )
        config = {"providers": [{"type": "dropmail", "enable": True}]}

        def fake_graphql(_provider, _token, query, variables=None, **_kwargs):
            if "domains" in query:
                return {
                    "domains": [
                        {"id": "preferred", "name": "preferred.test"},
                        {"id": "backup", "name": "backup.test"},
                    ]
                }
            self.assertEqual(variables["input"]["domainId"], "backup")
            return {
                "introduceSession": {
                    "id": "session-backup",
                    "addresses": [{"id": "address-backup", "address": "new@backup.test"}],
                }
            }

        with (
            patch.object(mail_provider, "provider_index", 0),
            patch.object(
                dropmail_provider.DropMailProvider,
                "_generate_token",
                return_value="dynamic-token",
            ),
            patch.object(
                dropmail_provider.DropMailProvider,
                "_graphql",
                autospec=True,
                side_effect=fake_graphql,
            ),
            patch.object(dropmail_provider.random, "shuffle", lambda values: None),
        ):
            mailbox = mail_provider.create_mailbox(
                config,
                excluded_domains={"preferred.test"},
            )

        self.assertEqual(mailbox["address"], "new@backup.test")
        self.assertIn("preferred.test", mailbox["retry_excluded_domains_checked"])

    def test_dropmail_fetches_latest_message_for_the_created_address(self):
        provider = self._dropmail()
        provider._graphql = lambda *args, **kwargs: {
            "session": {
                "mails": [
                    {
                        "id": "old",
                        "fromAddr": "first@example.com",
                        "toAddr": "user@random.test",
                        "receivedAt": "2026-07-18T10:00:00Z",
                        "text": "old",
                        "html": "",
                        "headerSubject": "old",
                    },
                    {
                        "id": "new",
                        "fromAddr": "noreply@example.com",
                        "toAddr": "user@random.test",
                        "receivedAt": "2026-07-18T10:01:00Z",
                        "text": "Verification code: 123456",
                        "html": "",
                        "headerSubject": "Verify",
                    },
                ]
            }
        }
        try:
            message = provider.fetch_latest_message(
                {
                    "address": "user@random.test",
                    "api_base": "https://dropmail.me",
                    "api_token": "token",
                    "session_id": "session",
                }
            )
        finally:
            provider.close()

        self.assertIsNotNone(message)
        self.assertEqual(message["message_id"], "new")
        self.assertEqual(mail_provider._extract_code(message), "123456")

    def test_dropmail_prefers_a_domain_with_success_history_after_reordering(self):
        mail_provider.mark_mailbox_result(
            {
                "provider": "dropmail",
                "provider_ref": "dropmail#3",
                "address": "old@preferred.test",
                "domain_family": "preferred.test",
                "random_domain": True,
            },
            success=True,
        )
        provider = self._dropmail()
        provider._generate_token = lambda: "dynamic-token"

        def fake_graphql(token, query, variables=None, **kwargs):
            if "domains" in query:
                return {
                    "domains": [
                        {"id": "unseen", "name": "unseen.test"},
                        {"id": "preferred", "name": "preferred.test"},
                    ]
                }
            self.assertEqual(variables["input"]["domainId"], "preferred")
            return {
                "introduceSession": {
                    "id": "session-preferred",
                    "addresses": [
                        {"id": "address-preferred", "address": "new@preferred.test"}
                    ],
                }
            }

        provider._graphql = fake_graphql
        try:
            with patch.object(dropmail_provider.random, "shuffle", lambda values: None):
                mailbox = provider.create_mailbox()
        finally:
            provider.close()

        self.assertEqual(mailbox["address"], "new@preferred.test")

    def test_dropmail_half_opens_best_domain_when_every_family_is_cooling(self):
        preferred = {
            "provider": "dropmail",
            "provider_ref": "dropmail#1",
            "address": "old@preferred.test",
            "domain_family": "preferred.test",
            "random_domain": True,
        }
        rejected = {
            **preferred,
            "address": "old@rejected.test",
            "domain_family": "rejected.test",
        }
        mail_provider.mark_mailbox_result(preferred, success=True)
        mail_provider.mark_mailbox_result(
            preferred,
            success=False,
            error="registration_disallowed",
        )
        mail_provider.mark_mailbox_result(
            rejected,
            success=False,
            error="unsupported_email",
        )
        provider = self._dropmail()
        provider._generate_token = lambda: "dynamic-token"

        def fake_graphql(token, query, variables=None, **kwargs):
            if "domains" in query:
                return {
                    "domains": [
                        {"id": "rejected", "name": "rejected.test"},
                        {"id": "preferred", "name": "preferred.test"},
                    ]
                }
            self.assertEqual(variables["input"]["domainId"], "preferred")
            return {
                "introduceSession": {
                    "id": "session-half-open",
                    "addresses": [
                        {"id": "address-half-open", "address": "new@preferred.test"}
                    ],
                }
            }

        provider._graphql = fake_graphql
        try:
            with patch.object(dropmail_provider.random, "shuffle", lambda values: None):
                mailbox = provider.create_mailbox()
        finally:
            provider.close()

        self.assertEqual(mailbox["address"], "new@preferred.test")
        self.assertTrue(mailbox["domain_health_half_open"])
        self.assertEqual(mailbox["domain_health_skipped"], ["rejected.test"])

    def test_dropmail_cooling_domains_fail_over_before_half_open_when_another_provider_exists(self):
        for domain in ("first.test", "second.test"):
            mail_provider.mark_mailbox_result(
                {
                    "provider": "dropmail",
                    "provider_ref": "dropmail#1",
                    "address": f"old@{domain}",
                    "domain_family": domain,
                    "random_domain": True,
                },
                success=False,
                error="unsupported_email",
            )

        def fake_graphql(_provider, _token, query, variables=None, **_kwargs):
            if "domains" in query:
                return {
                    "domains": [
                        {"id": "first", "name": "first.test"},
                        {"id": "second", "name": "second.test"},
                    ]
                }
            self.assertIsNotNone(variables)
            return {
                "introduceSession": {
                    "id": "session-half-open",
                    "addresses": [
                        {"id": "address-half-open", "address": "new@first.test"}
                    ],
                }
            }

        config = {
            "providers": [
                {"type": "dropmail", "enable": True},
                {"type": "duckmail", "enable": True, "api_key": "test"},
            ]
        }
        with (
            patch.object(mail_provider, "provider_index", 0),
            patch.object(dropmail_provider.DropMailProvider, "_generate_token", return_value="dynamic-token"),
            patch.object(dropmail_provider.DropMailProvider, "_graphql", autospec=True, side_effect=fake_graphql),
            patch.object(
                mail_provider.DuckMailProvider,
                "create_mailbox",
                return_value={"provider": "duckmail", "address": "backup@duckmail.sbs", "token": "token"},
            ),
        ):
            mailbox = mail_provider.create_mailbox(config)

        self.assertEqual(mailbox["provider"], "duckmail")
        self.assertTrue(mailbox["provider_failover"])
        self.assertIn("DropMail 随机域名均处于冷却", mailbox["provider_failover_from"][0])

    def test_dropmail_last_provider_half_opens_after_previous_provider_failed(self):
        for domain in ("first.test", "second.test"):
            mail_provider.mark_mailbox_result(
                {
                    "provider": "dropmail",
                    "provider_ref": "dropmail#2",
                    "address": f"old@{domain}",
                    "domain_family": domain,
                    "random_domain": True,
                },
                success=False,
                error="unsupported_email",
            )

        def fake_graphql(_provider, _token, query, variables=None, **_kwargs):
            if "domains" in query:
                return {
                    "domains": [
                        {"id": "first", "name": "first.test"},
                        {"id": "second", "name": "second.test"},
                    ]
                }
            self.assertIsNotNone(variables)
            return {
                "introduceSession": {
                    "id": "session-last-provider-half-open",
                    "addresses": [
                        {
                            "id": "address-half-open",
                            "address": "new@first.test",
                        }
                    ],
                }
            }

        config = {
            "providers": [
                {"type": "duckmail", "enable": True, "api_key": "test"},
                {"type": "dropmail", "enable": True},
            ]
        }
        with (
            patch.object(mail_provider, "provider_index", 0),
            patch.object(
                mail_provider.DuckMailProvider,
                "create_mailbox",
                side_effect=RuntimeError("duckmail unavailable"),
            ),
            patch.object(
                dropmail_provider.DropMailProvider,
                "_generate_token",
                return_value="dynamic-token",
            ),
            patch.object(
                dropmail_provider.DropMailProvider,
                "_graphql",
                autospec=True,
                side_effect=fake_graphql,
            ),
        ):
            mailbox = mail_provider.create_mailbox(config)

        self.assertEqual(mailbox["provider"], "dropmail")
        self.assertTrue(mailbox["domain_health_half_open"])
        self.assertTrue(mailbox["provider_failover"])
        self.assertIn("duckmail unavailable", mailbox["provider_failover_from"][0])

    def test_dropmail_reuses_a_valid_cached_api_token(self):
        first = self._dropmail()
        first._request_json = lambda *args, **kwargs: {"token": "shared-token"}
        try:
            self.assertEqual(first._generate_token(), "shared-token")
        finally:
            first.close()

        second = self._dropmail()
        second._request_json = lambda *args, **kwargs: self.fail("valid cached token should be reused")
        try:
            self.assertEqual(second._generate_token(), "shared-token")
        finally:
            second.close()

        cached = json.loads(self.dropmail_token_file.read_text(encoding="utf-8"))
        self.assertEqual(len(cached["items"]), 1)
        self.assertGreater(cached["items"][0]["expires_at"], cached["items"][0]["created_at"])

    def test_dropmail_refreshes_an_expired_cached_api_token(self):
        self.dropmail_token_file.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "items": [
                        {
                            "api_base": "https://dropmail.me",
                            "token_lifetime": "1d",
                            "token": "expired-token",
                            "created_at": 1,
                            "expires_at": 2,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        provider = self._dropmail()
        provider._request_json = lambda *args, **kwargs: {"token": "fresh-token"}
        try:
            self.assertEqual(provider._generate_token(), "fresh-token")
        finally:
            provider.close()

if __name__ == "__main__":
    unittest.main()
