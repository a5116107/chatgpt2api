import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("CHATGPT2API_AUTH_KEY", "chatgpt2api")

from services.register import mail_provider, random_mail_domain_health


class RandomMailDomainTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.health_file = Path(self.temp_dir.name) / "random_mail_domain_health.json"
        self.health_patch = patch.object(
            random_mail_domain_health,
            "RANDOM_MAIL_DOMAIN_HEALTH_FILE",
            self.health_file,
        )
        self.health_patch.start()

    def tearDown(self):
        self.health_patch.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def _tempmail(**entry_overrides):
        entry = {"provider_ref": "tempmail_lol#1", "domain": [], **entry_overrides}
        return mail_provider.TempMailLolProvider(entry, mail_provider._config({}))

    def test_tempmail_empty_domain_requests_service_random_domain(self):
        provider = self._tempmail()
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append(kwargs.get("payload"))
            return {"address": "user@r1.example.com", "token": "token-1"}

        provider._request = fake_request
        try:
            mailbox = provider.create_mailbox("user")
        finally:
            provider.close()

        self.assertEqual(calls, [{"prefix": "user"}])
        self.assertTrue(mailbox["random_domain"])
        self.assertEqual(mailbox["domain"], "r1.example.com")
        self.assertEqual(mailbox["domain_family"], "example.com")
        self.assertEqual(mailbox["label"], "tempmail_lol:r1.example.com")

    def test_tempmail_retries_a_transport_failure(self):
        provider = self._tempmail()
        calls = 0

        def fake_request(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("proxy connection reset")
            payload = {"address": "retry@example.com", "token": "token-1"}
            return SimpleNamespace(
                status_code=201,
                text=json.dumps(payload),
                json=lambda: payload,
            )

        provider.session.request = fake_request
        try:
            with patch.object(mail_provider.time, "sleep"):
                result = provider._request(
                    "POST",
                    "/inbox/create",
                    payload={},
                    expected=(200, 201),
                )
        finally:
            provider.close()

        self.assertEqual(calls, 2)
        self.assertEqual(result["address"], "retry@example.com")

    def test_tempmail_skips_a_cooling_domain_family(self):
        rejected = {
            "provider": "tempmail_lol",
            "provider_ref": "tempmail_lol#1",
            "address": "old@a.blocked.test",
            "domain_family": "blocked.test",
            "random_domain": True,
        }
        mail_provider.mark_mailbox_result(rejected, success=False, error="account_creation_failed")
        provider = self._tempmail(random_domain_attempts=3)
        responses = iter(
            [
                {"address": "first@b.blocked.test", "token": "discarded"},
                {"address": "second@c.healthy.test", "token": "accepted"},
            ]
        )
        provider._request = lambda *args, **kwargs: next(responses)
        try:
            mailbox = provider.create_mailbox()
        finally:
            provider.close()

        self.assertEqual(mailbox["address"], "second@c.healthy.test")
        self.assertEqual(mailbox["domain_health_skipped"], ["b.blocked.test"])

    def test_successful_domain_gets_one_generic_failure_retry_before_job_exclusion(self):
        mailbox = {
            "provider": "dropmail",
            "provider_ref": "dropmail#3",
            "address": "first@preferred.test",
            "domain": "preferred.test",
            "domain_family": "preferred.test",
            "random_domain": True,
        }
        mail_provider.mark_mailbox_result(mailbox, success=True)
        failures: dict[str, int] = {}

        first = mail_provider.mailbox_retry_excluded_domains(
            mailbox,
            "user_register_http_400: account_creation_failed",
            job_domain_failure_counts=failures,
        )
        second = mail_provider.mailbox_retry_excluded_domains(
            {**mailbox, "address": "second@preferred.test"},
            "user_register_http_400: account_creation_failed",
            job_domain_failure_counts=failures,
        )

        self.assertEqual(first, set())
        self.assertEqual(second, {"preferred.test"})

    def test_strong_domain_rejection_is_excluded_on_first_job_failure(self):
        mailbox = {
            "provider": "dropmail",
            "provider_ref": "dropmail#3",
            "address": "first@blocked.test",
            "domain": "blocked.test",
            "domain_family": "blocked.test",
            "random_domain": True,
        }

        excluded = mail_provider.mailbox_retry_excluded_domains(
            mailbox,
            "create_account_http_400: unsupported_email",
            job_domain_failure_counts={},
        )

        self.assertEqual(excluded, {"blocked.test"})

    def test_tempmail_half_opens_best_domain_when_every_family_is_cooling(self):
        preferred = {
            "provider": "tempmail_lol",
            "provider_ref": "tempmail_lol#1",
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
        provider = self._tempmail(random_domain_attempts=10)
        responses = iter(
            [
                {"address": "new@rejected.test", "token": "rejected-token"},
                {"address": "new@preferred.test", "token": "preferred-token"},
            ]
        )
        provider._request = lambda *args, **kwargs: next(responses)
        try:
            mailbox = provider.create_mailbox()
        finally:
            provider.close()

        self.assertEqual(mailbox["address"], "new@preferred.test")
        self.assertEqual(mailbox["token"], "preferred-token")
        self.assertTrue(mailbox["domain_health_half_open"])
        self.assertEqual(mailbox["domain_health_skipped"], ["rejected.test"])

    def test_success_clears_cooldown_and_health_is_provider_scoped(self):
        mailbox = {
            "provider": "tempmail_lol",
            "provider_ref": "tempmail_lol#1",
            "address": "user@r1.example.com",
            "domain_family": "example.com",
            "random_domain": True,
        }
        mail_provider.mark_mailbox_result(mailbox, success=False, error="registration_disallowed")
        self.assertTrue(
            random_mail_domain_health.random_mail_domain_is_cooling(
                "tempmail_lol", "tempmail_lol#1", "example.com"
            )
        )
        self.assertFalse(
            random_mail_domain_health.random_mail_domain_is_cooling(
                "dropmail", "dropmail#1", "example.com"
            )
        )

        mail_provider.mark_mailbox_result(mailbox, success=True)
        self.assertFalse(
            random_mail_domain_health.random_mail_domain_is_cooling(
                "tempmail_lol", "tempmail_lol#1", "example.com"
            )
        )
        key = "tempmail_lol|tempmail_lol#1|example.com"
        state = json.loads(self.health_file.read_text(encoding="utf-8"))["items"][key]
        self.assertEqual(state["success_count"], 1)
        self.assertEqual(state["failure_count"], 1)
        self.assertEqual(state["consecutive_failures"], 0)
        self.assertEqual(state["cooldown_until"], "")

    def test_random_domain_health_rolls_up_between_parent_and_child_families(self):
        child = {
            "provider": "tempmail_lol",
            "provider_ref": "tempmail_lol#1",
            "address": "user@a.blocked.test",
            "domain_family": "a.blocked.test",
            "random_domain": True,
        }
        mail_provider.mark_mailbox_result(child, success=False, error="account_creation_failed")
        self.assertTrue(
            random_mail_domain_health.random_mail_domain_is_cooling(
                "tempmail_lol", "tempmail_lol#1", "blocked.test"
            )
        )

        parent = {**child, "address": "user@parent.test", "domain_family": "parent.test"}
        mail_provider.mark_mailbox_result(parent, success=False, error="registration_disallowed")
        self.assertTrue(
            random_mail_domain_health.random_mail_domain_is_cooling(
                "tempmail_lol", "tempmail_lol#1", "b.parent.test"
            )
        )

    def test_health_survives_auto_provider_ref_reordering(self):
        successful = {
            "provider": "dropmail",
            "provider_ref": "dropmail#3",
            "address": "user@preferred.test",
            "domain_family": "preferred.test",
            "random_domain": True,
        }
        rejected = {
            **successful,
            "address": "user@blocked.test",
            "domain_family": "blocked.test",
        }
        mail_provider.mark_mailbox_result(successful, success=True)
        mail_provider.mark_mailbox_result(rejected, success=False, error="unsupported_email")

        self.assertGreater(
            random_mail_domain_health.random_mail_domain_priority(
                "dropmail", "dropmail#1", "preferred.test"
            ),
            random_mail_domain_health.random_mail_domain_priority(
                "dropmail", "dropmail#1", "unseen.test"
            ),
        )
        self.assertTrue(
            random_mail_domain_health.random_mail_domain_is_cooling(
                "dropmail", "dropmail#1", "blocked.test"
            )
        )

    def test_successful_domain_tolerates_one_generic_failure_after_ref_reordering(self):
        successful = {
            "provider": "dropmail",
            "provider_ref": "dropmail#3",
            "address": "user@preferred.test",
            "domain_family": "preferred.test",
            "random_domain": True,
        }
        generic_failure = {
            **successful,
            "provider_ref": "dropmail#2",
        }
        mail_provider.mark_mailbox_result(successful, success=True)
        mail_provider.mark_mailbox_result(
            generic_failure,
            success=False,
            error="account_creation_failed",
        )

        self.assertFalse(
            random_mail_domain_health.random_mail_domain_is_cooling(
                "dropmail", "dropmail#1", "preferred.test"
            )
        )

        mail_provider.mark_mailbox_result(
            generic_failure,
            success=False,
            error="account_creation_failed",
        )
        self.assertTrue(
            random_mail_domain_health.random_mail_domain_is_cooling(
                "dropmail", "dropmail#1", "preferred.test"
            )
        )

    def test_never_successful_repeated_generic_failures_remain_quarantined_after_cooldown(self):
        mailbox = {
            "provider": "tempmail_lol",
            "provider_ref": "tempmail_lol#1",
            "address": "user@blocked.test",
            "domain_family": "blocked.test",
            "random_domain": True,
        }
        for _ in range(3):
            mail_provider.mark_mailbox_result(
                mailbox,
                success=False,
                error="account_creation_failed",
            )

        health = json.loads(self.health_file.read_text(encoding="utf-8"))
        key = "tempmail_lol|tempmail_lol#1|blocked.test"
        health["items"][key]["cooldown_until"] = "2020-01-01T00:00:00+00:00"
        self.health_file.write_text(json.dumps(health), encoding="utf-8")

        self.assertTrue(
            random_mail_domain_health.random_mail_domain_is_permanently_rejected(
                "tempmail_lol",
                "tempmail_lol#1",
                "blocked.test",
            )
        )
        self.assertTrue(
            random_mail_domain_health.random_mail_domain_is_cooling(
                "tempmail_lol",
                "tempmail_lol#1",
                "blocked.test",
            )
        )

    def test_strong_domain_rejection_cools_a_previously_successful_domain(self):
        mailbox = {
            "provider": "dropmail",
            "provider_ref": "dropmail#3",
            "address": "user@preferred.test",
            "domain_family": "preferred.test",
            "random_domain": True,
        }
        mail_provider.mark_mailbox_result(mailbox, success=True)
        mail_provider.mark_mailbox_result(
            {**mailbox, "provider_ref": "dropmail#1"},
            success=False,
            error="unsupported_email",
        )

        self.assertTrue(
            random_mail_domain_health.random_mail_domain_is_cooling(
                "dropmail", "dropmail#2", "preferred.test"
            )
        )

    def test_country_not_supported_does_not_penalize_mail_domain(self):
        mailbox = {
            "provider": "dropmail",
            "provider_ref": "dropmail#1",
            "address": "user@healthy.test",
            "domain_family": "healthy.test",
            "random_domain": True,
        }

        mail_provider.mark_mailbox_result(
            mailbox,
            success=False,
            error="unsupported_country_region_territory - Country, region, or territory not supported",
        )

        self.assertFalse(self.health_file.exists())

    def test_tempmail_cooling_domains_fail_over_before_half_open_when_another_provider_exists(self):
        for domain in ("first.test", "second.test"):
            mail_provider.mark_mailbox_result(
                {
                    "provider": "tempmail_lol",
                    "provider_ref": "tempmail_lol#1",
                    "address": f"old@{domain}",
                    "domain_family": domain,
                    "random_domain": True,
                },
                success=False,
                error="unsupported_email",
            )
        config = {
            "providers": [
                {
                    "type": "tempmail_lol",
                    "enable": True,
                    "domain": [],
                    "random_domain_attempts": 2,
                },
                {"type": "duckmail", "enable": True, "api_key": "test"},
            ]
        }
        with (
            patch.object(mail_provider, "provider_index", 0),
            patch.object(
                mail_provider.TempMailLolProvider,
                "_request",
                side_effect=[
                    {"address": "new@first.test", "token": "first-token"},
                    {"address": "new@second.test", "token": "second-token"},
                ],
            ),
            patch.object(
                mail_provider.DuckMailProvider,
                "create_mailbox",
                return_value={"provider": "duckmail", "address": "backup@duckmail.sbs", "token": "token"},
            ),
        ):
            mailbox = mail_provider.create_mailbox(config)

        self.assertEqual(mailbox["provider"], "duckmail")
        self.assertTrue(mailbox["provider_failover"])
        self.assertIn("tempmail_lol#tempmail_lol#1", mailbox["provider_failover_from"][0])
        self.assertIn("随机域名均处于冷却", mailbox["provider_failover_from"][0])

    def test_tempmail_permanent_rejects_fail_over_after_cooldown_expires(self):
        for domain in ("first.test", "second.test"):
            mailbox = {
                "provider": "tempmail_lol",
                "provider_ref": "tempmail_lol#1",
                "address": f"old@{domain}",
                "domain_family": domain,
                "random_domain": True,
            }
            for _ in range(3):
                mail_provider.mark_mailbox_result(
                    mailbox,
                    success=False,
                    error="account_creation_failed",
                )

        health = json.loads(self.health_file.read_text(encoding="utf-8"))
        for entry in health["items"].values():
            entry["cooldown_until"] = "2020-01-01T00:00:00+00:00"
        self.health_file.write_text(json.dumps(health), encoding="utf-8")

        config = {
            "providers": [
                {
                    "type": "tempmail_lol",
                    "enable": True,
                    "domain": [],
                    "random_domain_attempts": 2,
                },
                {"type": "duckmail", "enable": True, "api_key": "test"},
            ]
        }
        with (
            patch.object(mail_provider, "provider_index", 0),
            patch.object(
                mail_provider.TempMailLolProvider,
                "_request",
                side_effect=[
                    {"address": "new@first.test", "token": "first-token"},
                    {"address": "new@second.test", "token": "second-token"},
                ],
            ),
            patch.object(
                mail_provider.DuckMailProvider,
                "create_mailbox",
                return_value={
                    "provider": "duckmail",
                    "address": "backup@duckmail.sbs",
                    "token": "token",
                },
            ),
        ):
            mailbox = mail_provider.create_mailbox(config)

        self.assertEqual(mailbox["provider"], "duckmail")
        self.assertTrue(mailbox["provider_failover"])
        self.assertIn("随机域名池已被上游永久拒绝", mailbox["provider_failover_from"][0])

    def test_tempmail_does_not_half_open_a_permanently_rejected_only_pool(self):
        rejected = {
            "provider": "tempmail_lol",
            "provider_ref": "tempmail_lol#1",
            "address": "old@blocked.test",
            "domain_family": "blocked.test",
            "random_domain": True,
        }
        for _ in range(3):
            mail_provider.mark_mailbox_result(
                rejected,
                success=False,
                error="account_creation_failed",
            )

        provider = self._tempmail(random_domain_attempts=2)
        provider._request = lambda *args, **kwargs: {
            "address": "new@blocked.test",
            "token": "blocked-token",
        }
        try:
            with self.assertRaisesRegex(RuntimeError, "随机域名池已被上游永久拒绝"):
                provider.create_mailbox()
        finally:
            provider.close()

if __name__ == "__main__":
    unittest.main()
