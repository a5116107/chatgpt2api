from __future__ import annotations

import unittest
import time
from unittest import mock

from services.account_service import AccountService
from services.account_service import config as account_service_config
from services.openai_backend_api import InvalidAccessTokenError
from services.image_account_pool import ImagePoolOutcome, ImagePoolState
from services.storage.base import StorageBackend


class MemoryStorage(StorageBackend):
    def __init__(self, accounts: list[dict] | None = None) -> None:
        self.accounts = [dict(item) for item in (accounts or [])]
        self.auth_keys: list[dict] = []

    def load_accounts(self) -> list[dict]:
        return [dict(item) for item in self.accounts]

    def save_accounts(self, accounts: list[dict]) -> None:
        self.accounts = [dict(item) for item in accounts]

    def load_auth_keys(self) -> list[dict]:
        return [dict(item) for item in self.auth_keys]

    def save_auth_keys(self, auth_keys: list[dict]) -> None:
        self.auth_keys = [dict(item) for item in auth_keys]

    def health_check(self) -> dict:
        return {"status": "ok"}

    def get_backend_info(self) -> dict:
        return {"type": "memory"}


def account(token: str, **updates: object) -> dict:
    item = {
        "access_token": token,
        "email": f"{token}@example.test",
        "status": "正常",
        "quota": 5,
        "type": "free",
        "profile_snapshot": {"status": "active"},
        "profile_status": "active",
    }
    item.update(updates)
    return item


class AccountTokenLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch(
            "services.account_service.runtime_profile_service.ensure_account_profile",
            side_effect=lambda item: (item, {}),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        sync_patcher = mock.patch.object(AccountService, "_sync_risk_control_capabilities")
        sync_patcher.start()
        self.addCleanup(sync_patcher.stop)

    def service(self, *accounts: dict) -> AccountService:
        return AccountService(MemoryStorage(list(accounts)))

    def test_chat_only_revocation_is_recoverable_but_image_revocation_is_terminal(self) -> None:
        self.assertFalse(
            AccountService._access_token_hard_dead(
                {"last_refresh_error": "text_stream:token_revoked"}
            )
        )
        for value in ("image_stream:token_revoked", "status=401 code=token_revoked"):
            with self.subTest(value=value):
                self.assertTrue(AccountService._access_token_hard_dead({"last_refresh_error": value}))

    def test_probe_revocation_field_prevents_candidate_selection(self) -> None:
        service = self.service(account("revoked", image_last_probe_error="status=401 code=token_revoked"))

        self.assertEqual(service._list_ready_candidate_tokens(), [])
        self.assertEqual(service.get_text_access_token(), "")
        with mock.patch.object(
            type(account_service_config),
            "auto_remove_invalid_accounts",
            new_callable=mock.PropertyMock,
            return_value=False,
        ):
            result = service.probe_image_candidates(limit=1)
        self.assertEqual(result["checked"], 0)
        self.assertEqual(result["quarantined"], 1)
        self.assertEqual(service.get_account("revoked")["status"], "禁用")

    def test_remove_revoked_token_quarantines_idempotently(self) -> None:
        service = self.service(account("revoked"))

        service.remove_invalid_token("revoked", "image_stream:token_revoked", quiet=True)
        first = service.get_account("revoked")
        service.remove_invalid_token("revoked", "text_stream:token_revoked", quiet=True)
        second = service.get_account("revoked")

        self.assertEqual(first["status"], "禁用")
        self.assertEqual(first["quota"], 0)
        self.assertEqual(first["token_status"], "revoked")
        self.assertTrue(first["token_revoked_at"])
        self.assertEqual(first["invalid_count"], 1)
        self.assertEqual(second["invalid_count"], 1)
        self.assertEqual(second["token_revoked_at"], first["token_revoked_at"])

    def test_probe_batches_rotate_across_the_whole_pool(self) -> None:
        service = self.service(*(account(f"token-{index}") for index in range(4)))
        visited: list[str] = []

        class FakeBackend:
            def __init__(self, token: str) -> None:
                visited.append(token)

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def set_image_request_context(self, *_args) -> None:
                return None

            def _bootstrap(self) -> None:
                return None

            def _get_chat_requirements(self) -> None:
                return None

        with mock.patch("services.openai_backend_api.OpenAIBackendAPI", FakeBackend):
            first = service.probe_image_candidates(limit=2)
            second = service.probe_image_candidates(limit=2)

        self.assertEqual(first["checked"], 2)
        self.assertEqual(second["checked"], 2)
        self.assertEqual(set(visited), {"token-0", "token-1", "token-2", "token-3"})

    def test_probe_quarantines_explicit_revocation(self) -> None:
        service = self.service(account("revoked", image_quota_updated_at=time.time()))

        class RevokedBackend:
            def __init__(self, _token: str) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def set_image_request_context(self, *_args) -> None:
                return None

            def _bootstrap(self) -> None:
                return None

            def _get_chat_requirements(self) -> None:
                raise RuntimeError("status=401 code=token_revoked")

        with (
            mock.patch("services.openai_backend_api.OpenAIBackendAPI", RevokedBackend),
            mock.patch.object(
                type(account_service_config),
                "auto_remove_invalid_accounts",
                new_callable=mock.PropertyMock,
                return_value=False,
            ),
        ):
            result = service.probe_image_candidates(limit=1)

        self.assertEqual(result["quarantined"], 1)
        self.assertEqual(service.get_account("revoked")["status"], "禁用")

    def test_successful_token_rotation_clears_terminal_markers(self) -> None:
        service = self.service(account(
            "old-token",
            status="禁用",
            quota=0,
            token_status="revoked",
            token_revoked=True,
            token_revoked_at="2026-01-01T00:00:00+00:00",
            token_revoked_source="image_stream:token_revoked",
        ))

        new_token = service._apply_refreshed_tokens(
            "old-token",
            {"access_token": "new-token", "refresh_token": "new-refresh"},
            "test_refresh",
        )

        refreshed = service.get_account(new_token)
        self.assertEqual(new_token, "new-token")
        self.assertEqual(refreshed["status"], "正常")
        self.assertEqual(refreshed["token_status"], "active")
        self.assertFalse(refreshed["token_revoked"])
        self.assertIsNone(refreshed.get("token_revoked_at"))
        self.assertIsNone(refreshed.get("token_revoked_source"))

    def test_registered_account_requires_and_records_durable_refresh(self) -> None:
        service = self.service(account("old-token", refresh_token="short-refresh"))

        def rotate(token: str, *, force: bool, event: str) -> str:
            self.assertEqual(token, "old-token")
            self.assertTrue(force)
            self.assertEqual(event, "register_token_acceptance")
            return service._apply_refreshed_tokens(
                token,
                {
                    "access_token": "durable-token",
                    "refresh_token": "durable-refresh",
                    "id_token": "durable-id",
                },
                event,
            )

        with mock.patch.object(service, "refresh_access_token", side_effect=rotate):
            accepted = service.accept_registered_account("old-token")

        self.assertEqual(accepted["access_token"], "durable-token")
        self.assertEqual(accepted["refresh_token"], "durable-refresh")
        self.assertTrue(accepted["registration_token_rotated"])
        self.assertTrue(accepted["registration_token_validated_at"])
        self.assertEqual(service.list_tokens(), ["durable-token"])
        self.assertEqual(service.get_account("old-token")["access_token"], "durable-token")

    def test_registered_account_is_removed_when_refresh_does_not_persist(self) -> None:
        service = self.service(account("new-token", refresh_token="short-refresh"))

        with mock.patch.object(service, "refresh_access_token", return_value="new-token"):
            with self.assertRaisesRegex(RuntimeError, "register_token_acceptance_failed"):
                service.accept_registered_account("new-token")

        self.assertIsNone(service.get_account("new-token"))

    def test_refresh_uses_oauth_metadata_persisted_on_the_account(self) -> None:
        service = self.service(account(
            "access-token",
            refresh_token="refresh-token",
            oauth_client_id="account-client-id",
            oauth_token_url="https://auth.example.test/oauth/token",
        ))
        response = mock.Mock()
        response.status_code = 200
        response.text = '{"access_token":"next-access"}'
        response.json.return_value = {
            "access_token": "next-access",
            "refresh_token": "next-refresh",
        }
        session = mock.Mock()
        session.post.return_value = response

        with mock.patch("curl_cffi.requests.Session", return_value=session):
            result = service._request_access_token_refresh(
                "refresh-token",
                service.get_account("access-token"),
            )

        self.assertEqual(result["access_token"], "next-access")
        request = session.post.call_args
        self.assertEqual(request.args[0], "https://auth.example.test/oauth/token")
        self.assertEqual(request.kwargs["data"]["client_id"], "account-client-id")
        session.close.assert_called_once_with()

    def test_backend_access_token_failure_does_not_remove_account_after_ambiguous_refresh_401(self) -> None:
        service = self.service(account("access-token", refresh_token="refresh-token"))

        class InvalidBackend:
            def __init__(self, _token: str) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def get_user_info(self) -> dict:
                raise InvalidAccessTokenError("token invalidated (/backend-api/me)")

        with (
            mock.patch("services.openai_backend_api.OpenAIBackendAPI", InvalidBackend),
            mock.patch.object(
                service,
                "_request_access_token_refresh",
                side_effect=RuntimeError("oauth_refresh_http_401: upstream unauthorized"),
            ),
            mock.patch.object(
                type(account_service_config),
                "auto_remove_invalid_accounts",
                new_callable=mock.PropertyMock,
                return_value=True,
            ),
        ):
            with self.assertRaises(InvalidAccessTokenError):
                service.fetch_remote_info(
                    "access-token",
                    event="image_quota_probe",
                    defer_invalid_removal=False,
                )

        retained = service.get_account("access-token")
        self.assertIsNotNone(retained)
        self.assertEqual(retained["auth_state"], "cooldown")
        self.assertTrue(retained["auth_recovery_at"])
        self.assertGreaterEqual(retained["invalid_count"], 1)

    def test_image_probe_keeps_account_when_refresh_fails_with_tls_error(self) -> None:
        service = self.service(account("access-token", refresh_token="refresh-token"))

        class InvalidBackend:
            def __init__(self, _token: str) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def get_user_info(self) -> dict:
                raise InvalidAccessTokenError("token invalidated (/backend-api/me)")

        with (
            mock.patch("services.openai_backend_api.OpenAIBackendAPI", InvalidBackend),
            mock.patch.object(
                service,
                "_request_access_token_refresh",
                side_effect=RuntimeError("curl: (35) TLS connect error"),
            ),
            mock.patch.object(
                type(account_service_config),
                "auto_remove_invalid_accounts",
                new_callable=mock.PropertyMock,
                return_value=True,
            ),
        ):
            result = service.probe_image_candidates(limit=1)

        retained = service.get_account("access-token")
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["removed"], 0)
        self.assertEqual(result["quarantined"], 0)
        self.assertIsNotNone(retained)
        self.assertEqual(retained["auth_state"], "cooldown")
        self.assertEqual(retained["refresh_token_state"], "active")
        self.assertEqual(retained["refresh_token_permanent_failures"], 0)
        self.assertIn("TLS connect error", retained["last_token_refresh_error"])
        self.assertNotEqual(retained["image_pool_state"], ImagePoolState.QUARANTINED)

    def test_backend_probe_retries_after_successful_same_token_refresh(self) -> None:
        service = self.service(account("same-token", refresh_token="refresh-token"))
        backend_calls: list[str] = []

        class SameTokenBackend:
            def __init__(self, token: str) -> None:
                self.token = token

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def get_user_info(self) -> dict:
                backend_calls.append(self.token)
                if len(backend_calls) == 1:
                    raise InvalidAccessTokenError("token invalidated (/backend-api/me)")
                return {"email": "same-token@example.test", "quota": 5}

        with (
            mock.patch("services.openai_backend_api.OpenAIBackendAPI", SameTokenBackend),
            mock.patch.object(
                service,
                "_request_access_token_refresh",
                return_value={
                    "access_token": "same-token",
                    "refresh_token": "refresh-token",
                    "id_token": "",
                },
            ),
        ):
            refreshed = service.fetch_remote_info("same-token", event="same_token_test")

        self.assertEqual(backend_calls, ["same-token", "same-token"])
        self.assertEqual(refreshed["auth_state"], "active")
        self.assertIsNone(refreshed["last_token_refresh_error"])

    def test_permanent_refresh_failure_requires_two_confirmations_before_removal(self) -> None:
        for error in (
            "oauth_refresh_http_400: invalid_grant",
            "oauth_refresh_http_401: {'code': 'token_invalid'}",
        ):
            with self.subTest(error=error):
                service = self.service(
                    account("access-token", refresh_token="refresh-token")
                )

                with (
                    mock.patch.object(
                        service,
                        "_request_access_token_refresh",
                        side_effect=RuntimeError(error),
                    ),
                    mock.patch.object(
                        type(account_service_config),
                        "auto_remove_invalid_accounts",
                        new_callable=mock.PropertyMock,
                        return_value=True,
                    ),
                ):
                    first = service.refresh_access_token(
                        "access-token",
                        force=True,
                        event="refresh_token_keepalive",
                    )
                    suspect = service.get_account(first)
                    self.assertIsNotNone(suspect)
                    self.assertEqual(suspect["refresh_token_state"], "suspect")
                    self.assertEqual(
                        suspect["refresh_token_permanent_failures"], 1
                    )

                    service.refresh_access_token(
                        "access-token",
                        force=True,
                        event="refresh_token_keepalive",
                    )

                self.assertIsNone(service.get_account("access-token"))

    def test_remote_probe_cannot_bypass_permanent_refresh_confirmation(self) -> None:
        service = self.service(
            account("access-token", refresh_token="refresh-token")
        )

        class InvalidBackend:
            def __init__(self, _token: str) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def get_user_info(self) -> dict:
                raise InvalidAccessTokenError("token invalidated (/backend-api/me)")

        with (
            mock.patch("services.openai_backend_api.OpenAIBackendAPI", InvalidBackend),
            mock.patch.object(
                service,
                "_request_access_token_refresh",
                side_effect=RuntimeError(
                    "oauth_refresh_http_401: {'code': 'token_invalid'}"
                ),
            ),
            mock.patch.object(
                type(account_service_config),
                "auto_remove_invalid_accounts",
                new_callable=mock.PropertyMock,
                return_value=True,
            ),
        ):
            with self.assertRaises(InvalidAccessTokenError):
                service.fetch_remote_info(
                    "access-token",
                    event="image_quota_probe",
                    defer_invalid_removal=False,
                )

            suspect = service.get_account("access-token")
            self.assertIsNotNone(suspect)
            self.assertEqual(suspect["refresh_token_state"], "suspect")
            self.assertEqual(suspect["refresh_token_permanent_failures"], 1)

            with self.assertRaises(InvalidAccessTokenError):
                service.fetch_remote_info(
                    "access-token",
                    event="image_quota_probe",
                    defer_invalid_removal=False,
                )

        self.assertIsNone(service.get_account("access-token"))

    def test_image_probe_requires_two_permanent_refresh_confirmations(self) -> None:
        service = self.service(
            account(
                "access-token",
                refresh_token="refresh-token",
                image_quota_updated_at=0,
            )
        )

        class InvalidBackend:
            def __init__(self, _token: str) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def get_user_info(self) -> dict:
                raise InvalidAccessTokenError("token invalidated (/backend-api/me)")

        with (
            mock.patch("services.openai_backend_api.OpenAIBackendAPI", InvalidBackend),
            mock.patch.object(
                service,
                "_request_access_token_refresh",
                side_effect=RuntimeError(
                    "oauth_refresh_http_401: {'code': 'token_invalid'}"
                ),
            ),
            mock.patch.object(
                type(account_service_config),
                "auto_remove_invalid_accounts",
                new_callable=mock.PropertyMock,
                return_value=True,
            ),
        ):
            first = service.probe_image_candidates(limit=1)

            suspect = service.get_account("access-token")
            self.assertEqual(first["checked"], 1)
            self.assertEqual(first["removed"], 0)
            self.assertEqual(first["quarantined"], 0)
            self.assertIsNotNone(suspect)
            self.assertEqual(suspect["refresh_token_state"], "suspect")
            self.assertEqual(suspect["refresh_token_permanent_failures"], 1)
            self.assertNotEqual(
                suspect["image_pool_state"],
                ImagePoolState.QUARANTINED,
            )

            service.update_account(
                "access-token",
                {
                    "auth_recovery_at": "2026-01-01T00:00:00+00:00",
                    "image_next_probe_at": 0,
                    "image_quota_updated_at": 0,
                },
                quiet=True,
                sync_capabilities=False,
            )
            second = service.probe_image_candidates(limit=1)

        self.assertEqual(second["checked"], 1)
        self.assertEqual(second["removed"], 1)
        self.assertIsNone(service.get_account("access-token"))

    def test_successful_refresh_clears_soft_auth_recovery_state(self) -> None:
        service = self.service(account(
            "old-token",
            status="异常",
            auth_state="cooldown",
            auth_recovery_at="2026-01-01T00:10:00+00:00",
            refresh_token_state="suspect",
            refresh_token_permanent_failures=1,
            refresh_token="old-refresh",
            invalid_count=2,
        ))

        new_token = service._apply_refreshed_tokens(
            "old-token",
            {"access_token": "new-token", "refresh_token": "new-refresh"},
            "test_recovery",
        )

        recovered = service.get_account(new_token)
        self.assertEqual(recovered["status"], "正常")
        self.assertEqual(recovered["auth_state"], "active")
        self.assertIsNone(recovered["auth_recovery_at"])
        self.assertEqual(recovered["refresh_token_state"], "active")
        self.assertEqual(recovered["refresh_token_permanent_failures"], 0)
        self.assertEqual(recovered["invalid_count"], 0)

    def test_successful_account_probe_clears_stale_image_revocation_state(self) -> None:
        service = self.service(account(
            "recovered-token",
            status="禁用",
            token_status="revoked",
            token_revoked=True,
            token_revoked_at="2026-01-01T00:00:00+00:00",
            token_revoked_source="image_probe",
            image_last_probe_error="status=401 code=token_revoked",
            image_pool_state=ImagePoolState.QUARANTINED,
            image_pool_reason="token_revoked",
        ))

        service._record_refresh_success("recovered-token")

        recovered = service.get_account("recovered-token")
        self.assertEqual(recovered["status"], "正常")
        self.assertEqual(recovered["token_status"], "active")
        self.assertFalse(recovered["token_revoked"])
        self.assertIsNone(recovered.get("token_revoked_at"))
        self.assertIsNone(recovered["image_last_probe_error"])
        self.assertEqual(recovered["image_pool_state"], ImagePoolState.PROBATION)
        self.assertEqual(recovered["image_pool_reason"], "account_probe_recovered")

    def test_transient_image_auth_failure_enters_recovery_not_quarantine(self) -> None:
        service = self.service(account(
            "recoverable-image-token",
            refresh_token="refresh-token",
            auth_state="cooldown",
            auth_recovery_at="2026-01-01T00:00:00+00:00",
            last_token_refresh_error="oauth_refresh_http_401: upstream unauthorized",
        ))

        updated = service.mark_image_probe_result(
            "recoverable-image-token",
            success=False,
            duration_ms=900,
            error="status=401 code=token_invalidated",
            outcome=ImagePoolOutcome.TOKEN_INVALID,
        )

        self.assertIsNotNone(updated)
        self.assertEqual(updated["image_pool_state"], ImagePoolState.PROBATION)
        self.assertFalse(updated.get("token_revoked", False))

    def test_auth_recovery_watcher_only_returns_expired_recoverable_accounts(self) -> None:
        service = self.service(
            account(
                "ready-recovery",
                refresh_token="refresh-ready",
                auth_state="cooldown",
                auth_recovery_at="2026-01-01T00:00:00+00:00",
            ),
            account(
                "future-recovery",
                refresh_token="refresh-future",
                auth_state="cooldown",
                auth_recovery_at="2999-01-01T00:00:00+00:00",
            ),
            account(
                "terminal-recovery",
                refresh_token="refresh-terminal",
                auth_state="invalidated",
                refresh_token_state="invalidated",
                refresh_token_permanent_failures=2,
            ),
        )

        self.assertEqual(service.list_auth_recovery_tokens(), ["ready-recovery"])

    def test_image_selection_honors_exclusions_and_detects_alternatives(self) -> None:
        service = self.service(account("first"), account("second"))

        selected = service.get_available_access_token(excluded_tokens={"first"})

        self.assertEqual(selected, "second")
        self.assertTrue(service.has_alternative_image_account("first"))
        self.assertFalse(service.has_alternative_image_account("first", excluded_tokens={"second"}))
        service.release_image_slot(selected)

    def test_image_selection_prefers_the_least_loaded_healthy_account(self) -> None:
        service = self.service(account("first"), account("second"))
        service._image_inflight["first"] = 1
        service._image_inflight_meta["first"] = [time.time()]

        with mock.patch.object(type(account_service_config), "image_account_concurrency", new_callable=mock.PropertyMock, return_value=3):
            selected = service.get_available_access_token()

        self.assertEqual(selected, "second")
        service.release_image_slot(selected)

    def test_image_slot_is_not_released_at_the_old_45_second_boundary(self) -> None:
        service = self.service(account("first"), account("second"))
        service._image_inflight["first"] = 1
        service._image_inflight_meta["first"] = [time.time() - 60.0]

        with (
            mock.patch.object(type(account_service_config), "image_account_concurrency", new_callable=mock.PropertyMock, return_value=1),
            mock.patch.object(type(account_service_config), "image_request_deadline_secs", new_callable=mock.PropertyMock, return_value=120.0),
        ):
            selected = service.get_available_access_token()

        self.assertEqual(selected, "second")
        self.assertEqual(service._image_inflight["first"], 1)
        service.release_image_slot(selected)


if __name__ == "__main__":
    unittest.main()
