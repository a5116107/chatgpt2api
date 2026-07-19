# AppSec Baseline

This project treats the following controls as release-entry requirements. The
evidence paths below are the current implementation and test surfaces; a
control is not considered complete when it is only described here.

| ID | Control | Current evidence | Acceptance check |
| --- | --- | --- | --- |
| APPSEC-AUTH-01 | Authentication and registration | `services/auth_service.py`, `api/support.py`, `test/test_openai_registration_retry.py` | Auth failures, retry limits, registration state transitions, and administrator authentication are covered by tests and runtime logs. |
| APPSEC-RBAC-02 | Role and resource authorization | `api/support.py`, protected routes in `api/` | Every administrator-only route calls the shared authorization guard; denial responses are tested through API contract tests. |
| APPSEC-SESSION-03 | Session and token lifecycle | `services/account_service.py`, `test/test_account_token_lifecycle.py` | Access/refresh tokens have explicit state, rotation, cooldown, and permanent-failure handling; invalid tokens are not silently reused. |
| APPSEC-SECRETS-04 | Secret handling | `services/config.py`, `utils/log.py`, `.gitignore`, `test/test_runtime_regressions.py` | Credentials and tokens stay in runtime configuration, are redacted in logs, and are absent from frontend source and test fixtures. |
| APPSEC-OWASP-05 | OWASP API and data-flow review | `api/`, `services/`, `test/` | API, proxy, upload, download, and account-pool changes are reviewed for access control, injection, SSRF, XSS, and misconfiguration before release. |
| APPSEC-SUPPLY-06 | Dependency and build provenance | `pyproject.toml`, `uv.lock`, `web/package.json`, `web/package-lock.json`, `Dockerfile` | Locked dependency resolution, reproducible image build inputs, and dependency checks are recorded for each release candidate. |
| APPSEC-SSRF-07 | Outbound URL and proxy controls | `services/proxy_service.py`, `services/register/mail_provider.py`, `test/test_runtime_regressions.py` | Outbound requests use explicit schemes, proxy settings, timeouts, and bounded redirects; private or malformed targets are rejected at the boundary. |
| APPSEC-XSS-08 | Browser output encoding | `web/src/`, `api/` response serializers | React rendering remains escaped by default; user text is not inserted through unreviewed HTML sinks, and API errors are serialized as data. |
| APPSEC-SQLI-09 | Query and command injection | `services/storage/`, `services/backup_service.py` | ORM and parameterized storage APIs are used for data queries; archive paths and filesystem paths are validated before access. |
| APPSEC-PERM-10 | Permission matrix | `api/support.py`, `api/accounts.py`, `api/system.py`, `test/test_runtime_regressions.py` | Administrator, authenticated, and unauthenticated paths have explicit allow/deny coverage; resource IDs are checked before state changes. |

## Release Checks

Run these checks from the repository root for every release candidate:

```text
node scripts/workflow/security-scan.mjs --format both
node scripts/workflow/secrets-scan.mjs --format both
node scripts/workflow/deps-guard.mjs --format both
```

The generated receipts must list all ten controls, report zero missing
controls, and retain any unresolved finding with an owner, mitigation, and
verification trigger.
