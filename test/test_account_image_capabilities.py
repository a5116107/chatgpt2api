from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("CHATGPT2API_AUTH_KEY", "test-auth")

from services.account_service import AccountService
from services.auth_service import AuthService
from services.config import config
from services.image_account_pool import ImagePoolOutcome, ImagePoolState
from services.runtime_profile_service import RuntimeProfileService
from services.storage.json_storage import JSONStorageBackend
from utils.helper import anonymize_token, split_image_model


class AccountCapabilityTests(unittest.TestCase):
    def test_runtime_profiles_are_deleted_in_one_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            profile_service = RuntimeProfileService(Path(tmp_dir) / "profiles.json")
            first = profile_service.create_profile(profile_id="profile-1")
            second = profile_service.create_profile(profile_id="profile-2")

            removed = profile_service.delete_by_accounts(
                [
                    {"runtime_profile_id": first["id"]},
                    {"runtime_profile_id": second["id"]},
                ]
            )

            self.assertEqual(removed, 2)
            self.assertEqual(profile_service.list_profiles(), [])

    def test_unknown_quota_accounts_are_available_only_when_not_throttled(self) -> None:
        self.assertFalse(
            AccountService._is_image_account_available(
                {"access_token": "token-1", "status": "限流", "image_quota_unknown": True, "quota": 0}
            )
        )
        self.assertTrue(
            AccountService._is_image_account_available(
                {"access_token": "token-1", "status": "正常", "image_quota_unknown": True, "quota": 0}
            )
        )

    def test_prolite_variants_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            self.assertEqual(service._normalize_account_type("prolite"), "ProLite")
            self.assertEqual(service._normalize_account_type("pro_lite"), "ProLite")

    def test_search_account_type_ignores_unrelated_scalar_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            self.assertIsNone(
                service._search_account_type(
                    {
                        "amr": ["pwd", "otp", "mfa"],
                        "chatgpt_compute_residency": "no_constraint",
                        "chatgpt_data_residency": "no_constraint",
                        "user_id": "user-I52GFfLGFM0dokFk2dBiKEBn",
                    }
                )
            )

    def test_mark_image_result_does_not_consume_unknown_quota(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_accounts(["token-1"])
            service.update_account(
                "token-1",
                {
                    "status": "正常",
                    "quota": 0,
                    "image_quota_unknown": True,
                },
            )

            updated = service.mark_image_result("token-1", success=True)

            self.assertIsNotNone(updated)
            self.assertEqual(updated["quota"], 0)
            self.assertEqual(updated["status"], "正常")
            self.assertTrue(updated["image_quota_unknown"])

    def test_split_image_model_supports_plan_type_prefix(self) -> None:
        self.assertEqual(split_image_model("gpt-image-2"), (None, "gpt-image-2"))
        self.assertEqual(split_image_model("plus-codex-gpt-image-2"), ("plus", "codex-gpt-image-2"))
        self.assertEqual(split_image_model("team-codex-gpt-image-2"), ("team", "codex-gpt-image-2"))
        self.assertEqual(split_image_model("pro-codex-gpt-image-2"), ("pro", "codex-gpt-image-2"))
        self.assertEqual(split_image_model("plus-gpt-image-2"), (None, None))
        self.assertEqual(split_image_model("unknown-image-model"), (None, None))

    def test_get_available_access_token_filters_by_plan_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_account_items(
                [
                    {"access_token": "token-plus", "type": "Plus", "status": "正常", "quota": 3},
                    {"access_token": "token-pro", "type": "Pro", "status": "正常", "quota": 3},
                ]
            )

            service.fetch_remote_info = lambda access_token, event="fetch_remote_info": service.get_account(access_token)

            plus_token = service.get_available_access_token(plan_type="plus")
            pro_token = service.get_available_access_token(plan_type="pro")
            service.release_image_slot(plus_token)
            service.release_image_slot(pro_token)

            self.assertEqual(plus_token, "token-plus")
            self.assertEqual(pro_token, "token-pro")

    def test_legacy_account_is_migrated_to_probation_pool_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_account_items(
                [{"access_token": "token-new", "status": "正常", "quota": 25}]
            )

            account = service.get_account("token-new")

            self.assertIsNotNone(account)
            self.assertEqual(account["image_pool_state"], ImagePoolState.PROBATION)
            self.assertEqual(account["image_quota_confidence"], "estimated")

    def test_manual_disable_and_chat_only_revoke_are_not_terminal_image_tokens(self) -> None:
        self.assertFalse(AccountService._access_token_hard_dead({"status": "禁用"}))
        self.assertFalse(
            AccountService._access_token_hard_dead(
                {"last_refresh_error": "text_stream:token_revoked"}
            )
        )
        self.assertTrue(
            AccountService._access_token_hard_dead(
                {"last_refresh_error": "refresh_token_invalidated"}
            )
        )

    def test_terminal_account_is_removed_but_ambiguous_failure_is_retained(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_account_items(
                [
                    {"access_token": "token-terminal", "status": "正常", "quota": 25},
                    {"access_token": "token-recoverable", "status": "正常", "quota": 25},
                ]
            )

            with mock.patch.object(
                type(config),
                "auto_remove_invalid_accounts",
                new_callable=mock.PropertyMock,
                return_value=True,
            ):
                removed = service.remove_invalid_token(
                    "token-terminal",
                    "image_stream:token_revoked",
                )
                retained = service.remove_invalid_token(
                    "token-recoverable",
                    "upstream proxy timeout",
                )

            self.assertTrue(removed)
            self.assertIsNone(service.get_account("token-terminal"))
            self.assertFalse(retained)
            self.assertEqual(service.get_account("token-recoverable")["status"], "异常")

    def test_terminal_sweep_removes_only_terminal_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_account_items(
                [
                    {
                        "access_token": "token-terminal",
                        "status": "禁用",
                        "quota": 0,
                        "token_status": "revoked",
                        "token_revoked": True,
                    },
                    {
                        "access_token": "token-exhausted",
                        "status": "限流",
                        "quota": 0,
                        "image_pool_state": ImagePoolState.EXHAUSTED,
                    },
                ]
            )

            with mock.patch.object(
                type(config),
                "auto_remove_invalid_accounts",
                new_callable=mock.PropertyMock,
                return_value=True,
            ):
                result = service.cleanup_persisted_terminal_accounts("test_sweep")

            self.assertEqual(result, {"removed": 1, "quarantined": 0})
            self.assertIsNone(service.get_account("token-terminal"))
            self.assertEqual(
                service.get_account("token-exhausted")["image_pool_state"],
                ImagePoolState.EXHAUSTED,
            )

    def test_rate_limited_account_enters_cooldown_and_leaves_scheduler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_account_items(
                [
                    {
                        "access_token": "token-rate-limited",
                        "status": "正常",
                        "quota": 25,
                        "image_pool_state": "ready",
                        "image_last_success_at": time.time() - 10,
                    },
                    {
                        "access_token": "token-ready",
                        "status": "正常",
                        "quota": 25,
                        "image_pool_state": "ready",
                        "image_last_success_at": time.time() - 10,
                    },
                ]
            )

            with mock.patch.dict(
                config.data,
                {"auto_remove_rate_limited_accounts": True},
            ):
                updated = service.mark_image_result(
                    "token-rate-limited",
                    success=False,
                    duration_ms=12_000,
                    outcome=ImagePoolOutcome.RATE_LIMITED,
                    error="poll returned 429",
                )
            candidates = service._list_available_candidate_tokens()

            self.assertIsNotNone(updated)
            self.assertEqual(updated["status"], "正常")
            self.assertEqual(updated["quota"], 25)
            self.assertEqual(updated["image_pool_state"], ImagePoolState.COOLDOWN)
            self.assertNotIn("token-rate-limited", candidates)
            self.assertIn("token-ready", candidates)

    def test_probe_scheduler_only_returns_due_nonterminal_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            future = int(time.time() + 3600)
            service.add_account_items(
                [
                    {
                        "access_token": "token-due",
                        "status": "正常",
                        "quota": 25,
                        "image_pool_state": "probation",
                    },
                    {
                        "access_token": "token-later",
                        "status": "正常",
                        "quota": 25,
                        "image_pool_state": "ready",
                        "image_next_probe_at": future,
                        "image_last_success_at": time.time() - 10,
                    },
                    {
                        "access_token": "token-revoked",
                        "status": "禁用",
                        "quota": 0,
                        "token_status": "revoked",
                        "token_revoked": True,
                    },
                ]
            )

            candidates = service._list_image_probe_candidate_tokens(10)

            self.assertEqual(candidates, ["token-due"])

    def test_probe_candidates_are_claimed_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_account_items(
                [
                    {"access_token": "token-1", "status": "正常", "quota": 25},
                    {"access_token": "token-2", "status": "正常", "quota": 25},
                ]
            )

            first = service._claim_image_probe_candidate_tokens(1)
            second = service._claim_image_probe_candidate_tokens(1)

            self.assertEqual(first, ["token-1"])
            self.assertEqual(second, ["token-2"])
            self.assertEqual(service._list_available_candidate_tokens(), [])

    def test_exhausted_probe_refreshes_remote_quota_and_reactivates_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_account_items(
                [
                    {
                        "access_token": "token-exhausted",
                        "status": "限流",
                        "quota": 0,
                        "image_pool_state": ImagePoolState.EXHAUSTED,
                    }
                ]
            )

            def refresh_quota(token: str, **_kwargs):
                return service.update_account(
                    token,
                    {
                        "status": "正常",
                        "quota": 5,
                        "image_quota_confidence": "verified",
                    },
                    quiet=True,
                )

            service.fetch_remote_info = refresh_quota
            result = service.probe_image_candidates(1)
            account = service.get_account("token-exhausted")

            self.assertEqual(result["checked"], 1)
            self.assertEqual(result["healthy"], 1)
            self.assertEqual(account["image_pool_state"], ImagePoolState.PROBATION)
            self.assertEqual(account["quota"], 5)

    def test_stale_quota_is_reconciled_during_a_normal_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_account_items(
                [
                    {
                        "access_token": "token-stale-quota",
                        "status": "正常",
                        "quota": 8,
                        "image_pool_state": ImagePoolState.READY,
                        "image_last_success_at": time.time() - 10,
                        "image_quota_updated_at": "2000-01-01T00:00:00+00:00",
                    }
                ]
            )
            refresh_calls: list[str] = []

            def refresh_quota(token: str, **_kwargs):
                refresh_calls.append(token)
                return service.update_account(
                    token,
                    {
                        "status": "正常",
                        "quota": 3,
                        "image_quota_confidence": "verified",
                        "image_quota_updated_at": "2099-01-01T00:00:00+00:00",
                    },
                    quiet=True,
                )

            service.fetch_remote_info = refresh_quota
            result = service.probe_image_candidates(1)
            account = service.get_account("token-stale-quota")

            self.assertEqual(refresh_calls, ["token-stale-quota"])
            self.assertEqual(result["healthy"], 1)
            self.assertEqual(account["quota"], 3)
            self.assertEqual(account["image_quota_confidence"], "verified")

    def test_pool_stats_report_schedulable_capacity_and_quota_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_account_items(
                [
                    {
                        "access_token": "token-ready",
                        "status": "正常",
                        "quota": 10,
                        "image_pool_state": "ready",
                        "image_last_success_at": time.time() - 10,
                        "image_quota_confidence": "verified",
                    },
                    {
                        "access_token": "token-probation",
                        "status": "正常",
                        "quota": 5,
                        "image_pool_state": "probation",
                    },
                    {
                        "access_token": "token-disabled",
                        "status": "禁用",
                        "quota": 0,
                    },
                ]
            )

            stats = service.get_stats()

            self.assertEqual(stats["image_pool_states"]["ready"], 1)
            self.assertEqual(stats["image_pool_states"]["probation"], 1)
            self.assertEqual(stats["image_pool_states"]["disabled"], 1)
            self.assertEqual(stats["image_schedulable_accounts"], 2)
            self.assertEqual(stats["image_schedulable_quota"], 15)
            self.assertEqual(stats["image_verified_quota"], 10)
            self.assertEqual(stats["image_quota_confidence"], {"verified": 1, "estimated": 1, "unknown": 0})

    def test_pool_stats_use_inflight_and_probe_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_account_items(
                [
                    {
                        "access_token": "token-ready",
                        "status": "正常",
                        "quota": 10,
                        "image_pool_state": "ready",
                        "image_last_success_at": time.time() - 10,
                    }
                ]
            )
            service._image_inflight["token-ready"] = 2
            service._image_probe_inflight.add("token-ready")

            stats = service.get_stats()

            self.assertEqual(stats["image_available_slots"], 1)
            self.assertEqual(stats["image_probe_due"], 0)
            self.assertEqual(stats["image_probe_inflight"], 1)


class TokenLogTests(unittest.TestCase):
    def test_anonymize_token_hides_raw_value(self) -> None:
        token = "super-secret-token"
        token_ref = anonymize_token(token)

        self.assertTrue(token_ref.startswith("token:"))
        self.assertNotIn(token, token_ref)


class AuthServiceTests(unittest.TestCase):
    def test_create_authenticate_disable_and_delete_user_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))

            item, raw_key = service.create_key(role="user", name="Alice")

            self.assertEqual(item["role"], "user")
            self.assertEqual(item["name"], "Alice")
            self.assertTrue(item["enabled"])
            self.assertTrue(raw_key.startswith("sk-"))

            authed = service.authenticate(raw_key)
            self.assertIsNotNone(authed)
            self.assertEqual(authed["id"], item["id"])
            self.assertEqual(authed["role"], "user")
            self.assertIsNotNone(authed["last_used_at"])

            updated = service.update_key(item["id"], {"enabled": False}, role="user")
            self.assertIsNotNone(updated)
            self.assertFalse(updated["enabled"])
            self.assertIsNone(service.authenticate(raw_key))

            self.assertTrue(service.delete_key(item["id"], role="user"))
            self.assertFalse(service.delete_key(item["id"], role="user"))
            self.assertEqual(service.list_keys(role="user"), [])

    def test_authenticate_ignores_last_used_save_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))
            item, raw_key = service.create_key(role="user", name="Alice")

            def fail_save() -> None:
                raise OSError("disk unavailable")

            service._save = fail_save

            authed = service.authenticate(raw_key)

            self.assertIsNotNone(authed)
            self.assertEqual(authed["id"], item["id"])
            self.assertIsNotNone(authed["last_used_at"])

    def test_update_user_key_replaces_raw_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))
            item, raw_key = service.create_key(role="user", name="Alice")

            updated = service.update_key(item["id"], {"key": "sk-user-custom-key"}, role="user")

            self.assertIsNotNone(updated)
            self.assertIsNone(service.authenticate(raw_key))

            authed = service.authenticate("sk-user-custom-key")
            self.assertIsNotNone(authed)
            self.assertEqual(authed["id"], item["id"])

    def test_user_key_name_must_be_unique(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))
            first, _ = service.create_key(role="user", name="Alice")
            second, _ = service.create_key(role="user", name="Bob")

            with self.assertRaisesRegex(ValueError, "这个名称已经在使用中了"):
                service.create_key(role="user", name="Alice")

            with self.assertRaisesRegex(ValueError, "这个名称已经在使用中了"):
                service.update_key(second["id"], {"name": "Alice"}, role="user")

            updated = service.update_key(first["id"], {"name": "Alice"}, role="user")
            self.assertIsNotNone(updated)
            self.assertEqual(updated["name"], "Alice")


if __name__ == "__main__":
    unittest.main()
