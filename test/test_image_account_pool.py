from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.config import ConfigStore
from services.image_account_pool import (
    ImagePoolOutcome,
    ImagePoolState,
    apply_image_outcome,
    apply_probe_result,
    classify_image_outcome,
    image_pool_score,
    is_image_pool_schedulable,
    is_terminal_image_token,
    normalize_image_pool_fields,
    probe_is_due,
    quota_refresh_is_due,
)


NOW = 2_000_000_000.0


class ImageAccountPoolPolicyTests(unittest.TestCase):
    def test_normalizes_legacy_accounts_without_breaking_status_compatibility(self) -> None:
        quarantined = normalize_image_pool_fields(
            {"status": "禁用", "token_status": "revoked", "quota": 0},
            now_epoch=NOW,
        )
        exhausted = normalize_image_pool_fields(
            {"status": "限流", "quota": 0},
            now_epoch=NOW,
        )
        unverified = normalize_image_pool_fields(
            {"status": "正常", "quota": 25, "image_probe_samples": 0},
            now_epoch=NOW,
        )
        probed_only = normalize_image_pool_fields(
            {
                "status": "正常",
                "quota": 25,
                "image_probe_samples": 2,
                "image_last_probe_error": None,
            },
            now_epoch=NOW,
        )
        generation_verified = normalize_image_pool_fields(
            {
                "status": "正常",
                "quota": 25,
                "image_pool_state": ImagePoolState.READY,
                "image_last_success_at": NOW - 10,
            },
            now_epoch=NOW,
        )

        self.assertEqual(quarantined["image_pool_state"], ImagePoolState.QUARANTINED)
        self.assertEqual(exhausted["image_pool_state"], ImagePoolState.EXHAUSTED)
        self.assertEqual(unverified["image_pool_state"], ImagePoolState.PROBATION)
        self.assertEqual(probed_only["image_pool_state"], ImagePoolState.PROBATION)
        self.assertEqual(generation_verified["image_pool_state"], ImagePoolState.READY)

    def test_classifies_account_outcomes_without_conflating_policy_and_network_errors(self) -> None:
        self.assertEqual(
            classify_image_outcome("Encountered invalidated oauth token", status_code=401),
            ImagePoolOutcome.TOKEN_INVALID,
        )
        self.assertEqual(
            classify_image_outcome("Too many requests", status_code=429),
            ImagePoolOutcome.RATE_LIMITED,
        )
        self.assertEqual(
            classify_image_outcome("insufficient_quota"),
            ImagePoolOutcome.QUOTA_EXHAUSTED,
        )
        self.assertEqual(
            classify_image_outcome("image request deadline exceeded"),
            ImagePoolOutcome.TIMEOUT,
        )
        self.assertEqual(
            classify_image_outcome("content policy violation"),
            ImagePoolOutcome.POLICY_REJECTED,
        )
        self.assertEqual(
            classify_image_outcome("upstream access denied", status_code=403),
            ImagePoolOutcome.UPSTREAM_ERROR,
        )

    def test_terminal_detection_does_not_conflate_manual_or_chat_only_states(self) -> None:
        self.assertFalse(is_terminal_image_token({"status": "禁用"}))
        self.assertFalse(
            is_terminal_image_token(
                {"last_refresh_error": "text_stream:token_revoked"}
            )
        )
        self.assertTrue(
            is_terminal_image_token(
                {"last_refresh_error": "oauth_refresh_http_401"}
            )
        )
        self.assertTrue(is_terminal_image_token({"token_revoked": True}))

    def test_remote_quota_refresh_deadline_uses_persisted_update_time(self) -> None:
        fresh = {"image_quota_updated_at": NOW - 299}
        stale = {"image_quota_updated_at": NOW - 1800}

        self.assertFalse(
            quota_refresh_is_due(fresh, now_epoch=NOW, interval_secs=300)
        )
        self.assertTrue(
            quota_refresh_is_due(stale, now_epoch=NOW, interval_secs=1800)
        )
        self.assertTrue(
            quota_refresh_is_due(
                {"image_pool_state": ImagePoolState.EXHAUSTED},
                now_epoch=NOW,
                interval_secs=1800,
            )
        )

    def test_rate_limit_uses_exponential_cooldown_and_is_not_schedulable(self) -> None:
        account = {"status": "正常", "quota": 25, "image_pool_state": "ready"}

        first = apply_image_outcome(
            account,
            ImagePoolOutcome.RATE_LIMITED,
            now_epoch=NOW,
            duration_ms=12_000,
            rate_limit_base_secs=600,
            max_cooldown_secs=3600,
        )
        second = apply_image_outcome(
            first,
            ImagePoolOutcome.RATE_LIMITED,
            now_epoch=NOW + 1,
            duration_ms=13_000,
            rate_limit_base_secs=600,
            max_cooldown_secs=3600,
        )

        self.assertEqual(first["image_pool_state"], ImagePoolState.COOLDOWN)
        self.assertEqual(first["image_cooldown_until"], int(NOW + 600))
        self.assertEqual(second["image_cooldown_until"], int(NOW + 1 + 1200))
        self.assertFalse(is_image_pool_schedulable(second, now_epoch=NOW + 10))
        self.assertTrue(is_image_pool_schedulable(second, now_epoch=NOW + 1300))

    def test_terminal_and_quota_outcomes_are_hard_gates(self) -> None:
        account = {"status": "正常", "quota": 25, "image_pool_state": "ready"}
        revoked = apply_image_outcome(
            account,
            ImagePoolOutcome.TOKEN_INVALID,
            now_epoch=NOW,
            error="token_revoked",
        )
        exhausted = apply_image_outcome(
            account,
            ImagePoolOutcome.QUOTA_EXHAUSTED,
            now_epoch=NOW,
            error="insufficient_quota",
        )

        self.assertEqual(revoked["status"], "禁用")
        self.assertEqual(revoked["token_status"], "revoked")
        self.assertEqual(revoked["image_pool_state"], ImagePoolState.QUARANTINED)
        self.assertFalse(is_image_pool_schedulable(revoked, now_epoch=NOW + 86400))
        self.assertEqual(exhausted["status"], "限流")
        self.assertEqual(exhausted["quota"], 0)
        self.assertEqual(exhausted["image_pool_state"], ImagePoolState.EXHAUSTED)

    def test_probe_uses_structured_outcome_when_error_text_is_ambiguous(self) -> None:
        updated = apply_probe_result(
            {"status": "正常", "quota": 25},
            success=False,
            now_epoch=NOW,
            duration_ms=500,
            error="upstream request failed",
            outcome=ImagePoolOutcome.TOKEN_INVALID,
        )

        self.assertEqual(updated["image_pool_state"], ImagePoolState.QUARANTINED)
        self.assertTrue(updated["token_revoked"])

    def test_remote_quota_refresh_reactivates_an_exhausted_account(self) -> None:
        recovered = normalize_image_pool_fields(
            {
                "status": "正常",
                "quota": 5,
                "image_pool_state": ImagePoolState.EXHAUSTED,
                "image_pool_reason": "quota_exhausted",
            },
            now_epoch=NOW,
        )

        self.assertEqual(recovered["image_pool_state"], ImagePoolState.PROBATION)
        self.assertEqual(recovered["image_pool_reason"], "quota_restored_awaiting_generation")
        self.assertTrue(is_image_pool_schedulable(recovered, now_epoch=NOW))

    def test_policy_rejection_does_not_reduce_account_health(self) -> None:
        account = {
            "status": "正常",
            "quota": 25,
            "image_pool_state": "ready",
            "image_last_success_at": NOW - 1,
            "image_health_samples": 4,
            "image_success_ema": 0.8,
            "image_consecutive_failures": 0,
            "fail": 1,
        }
        updated = apply_image_outcome(
            account,
            ImagePoolOutcome.POLICY_REJECTED,
            now_epoch=NOW,
            duration_ms=500,
            error="content policy violation",
        )

        self.assertEqual(updated["image_pool_state"], ImagePoolState.READY)
        self.assertEqual(updated["image_health_samples"], 4)
        self.assertEqual(updated["image_success_ema"], 0.8)
        self.assertEqual(updated["image_consecutive_failures"], 0)
        self.assertEqual(updated["fail"], 1)

    def test_probe_schedule_is_state_aware(self) -> None:
        healthy = apply_probe_result(
            {"status": "正常", "quota": 25},
            success=True,
            now_epoch=NOW,
            duration_ms=800,
            healthy_interval_secs=1800,
        )
        transient = apply_probe_result(
            {"status": "正常", "quota": 25},
            success=False,
            now_epoch=NOW,
            duration_ms=1500,
            error="upstream timeout",
            probation_interval_secs=60,
        )

        self.assertFalse(probe_is_due(healthy, now_epoch=NOW + 1799))
        self.assertTrue(probe_is_due(healthy, now_epoch=NOW + 1800))
        self.assertEqual(transient["image_pool_state"], ImagePoolState.PROBATION)
        self.assertFalse(probe_is_due(transient, now_epoch=NOW + 59))
        self.assertTrue(probe_is_due(transient, now_epoch=NOW + 60))

    def test_successful_probe_does_not_erase_a_real_generation_timeout(self) -> None:
        timed_out = apply_image_outcome(
            {
                "status": "正常",
                "quota": 25,
                "image_pool_state": ImagePoolState.READY,
                "image_last_success_at": NOW - 120,
            },
            ImagePoolOutcome.TIMEOUT,
            now_epoch=NOW,
            duration_ms=55_000,
            probation_interval_secs=60,
        )

        probed = apply_probe_result(
            timed_out,
            success=True,
            now_epoch=NOW + 60,
            duration_ms=800,
            healthy_interval_secs=1800,
        )

        self.assertEqual(probed["image_pool_state"], ImagePoolState.PROBATION)
        self.assertEqual(probed["image_pool_reason"], ImagePoolOutcome.TIMEOUT)
        self.assertEqual(probed["image_consecutive_failures"], 1)
        self.assertEqual(probed["image_next_probe_at"], int(NOW + 1860))

    def test_successful_probe_preserves_generation_verified_readiness(self) -> None:
        probed = apply_probe_result(
            {
                "status": "正常",
                "quota": 25,
                "image_pool_state": ImagePoolState.READY,
                "image_last_success_at": NOW - 10,
            },
            success=True,
            now_epoch=NOW,
            duration_ms=500,
        )

        self.assertEqual(probed["image_pool_state"], ImagePoolState.READY)
        self.assertIsNone(probed["image_pool_reason"])

    def test_successful_probe_of_exhausted_account_keeps_a_bounded_recheck_schedule(self) -> None:
        updated = apply_probe_result(
            {
                "status": "限流",
                "quota": 0,
                "image_pool_state": ImagePoolState.EXHAUSTED,
            },
            success=True,
            now_epoch=NOW,
            duration_ms=500,
            healthy_interval_secs=1800,
        )

        self.assertEqual(updated["image_pool_state"], ImagePoolState.EXHAUSTED)
        self.assertEqual(updated["status"], "限流")
        self.assertEqual(updated["image_next_probe_at"], int(NOW + 1800))
        self.assertFalse(probe_is_due(updated, now_epoch=NOW + 1799))

    def test_exhausted_account_uses_upstream_restore_time_for_recheck(self) -> None:
        updated = apply_image_outcome(
            {
                "status": "正常",
                "quota": 1,
                "image_pool_state": ImagePoolState.READY,
                "restore_at": NOW + 7200,
            },
            ImagePoolOutcome.QUOTA_EXHAUSTED,
            now_epoch=NOW,
            healthy_interval_secs=1800,
        )

        self.assertEqual(updated["image_next_probe_at"], int(NOW + 7200))

    def test_candidate_score_does_not_treat_a_light_probe_as_generation_ready(self) -> None:
        probed = {
            "image_probe_samples": 1,
            "image_last_probe_error": None,
            "quota": 10,
        }
        unknown = {"image_probe_samples": 0, "quota": 10}

        self.assertEqual(image_pool_score(probed, inflight=0)[0], 1)
        self.assertEqual(image_pool_score(unknown, inflight=0)[0], 1)

    def test_candidate_score_prefers_proven_fast_healthy_capacity(self) -> None:
        fast = {
            "image_pool_state": "ready",
            "image_success_ema": 0.9,
            "image_latency_ema_ms": 20_000,
            "image_consecutive_failures": 0,
            "quota": 10,
        }
        slow = {
            "image_pool_state": "ready",
            "image_success_ema": 0.6,
            "image_latency_ema_ms": 50_000,
            "image_consecutive_failures": 2,
            "quota": 25,
        }

        self.assertLess(image_pool_score(fast, inflight=0), image_pool_score(slow, inflight=0))

    def test_account_pool_configuration_is_bounded_and_exported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = ConfigStore(Path(tmp_dir) / "config.json")
            store.data.update(
                {
                    "image_account_probe_parallelism": 999,
                    "image_account_probe_healthy_interval_secs": 1,
                    "image_account_quota_refresh_interval_secs": 999999,
                    "image_account_probe_probation_interval_secs": 99999,
                    "image_account_rate_limit_cooldown_secs": 1,
                    "image_account_timeout_cooldown_secs": 99999,
                    "image_account_max_cooldown_secs": 1,
                    "image_account_failure_threshold": 999,
                    "auto_remove_rate_limited_accounts": True,
                }
            )

            exported = store.get()
            updated = store.update({"auto_remove_rate_limited_accounts": True})

        self.assertEqual(exported["image_account_probe_parallelism"], 10)
        self.assertEqual(exported["image_account_probe_healthy_interval_secs"], 300)
        self.assertEqual(exported["image_account_quota_refresh_interval_secs"], 86400)
        self.assertEqual(exported["image_account_probe_probation_interval_secs"], 3600)
        self.assertEqual(exported["image_account_rate_limit_cooldown_secs"], 60)
        self.assertEqual(exported["image_account_timeout_cooldown_secs"], 1800)
        self.assertEqual(exported["image_account_max_cooldown_secs"], 300)
        self.assertEqual(exported["image_account_failure_threshold"], 10)
        self.assertNotIn("auto_remove_rate_limited_accounts", exported)
        self.assertNotIn("auto_remove_rate_limited_accounts", updated)
        self.assertNotIn("auto_remove_rate_limited_accounts", store.data)


if __name__ == "__main__":
    unittest.main()
