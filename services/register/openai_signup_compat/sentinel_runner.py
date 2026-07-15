from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import SignupConfig

logger = logging.getLogger(__name__)

_FLOW_PAGE_URL = {
    "username_password_create": "https://auth.openai.com/create-account/password",
    "authorize_continue": "https://auth.openai.com/email-verification",
    "oauth_create_account": "https://auth.openai.com/about-you",
}
_DEFAULT_OBSERVER_WAIT_MS = 5000


@dataclass(frozen=True)
class SentinelHeaders:
    token: str
    so_token: str | None = None
    token_len: int = 0
    so_len: int = 0
    so_present: bool = False
    route: str = "legacy"
    flow: str = ""
    sdk: str = ""
    observer_wait_ms: int = 0
    so_attempted: bool = False
    so_init_attempted: bool = False
    so_error: str = ""


def _node_executable() -> str:
    return os.environ.get("NODE_EXECUTABLE") or ("node.exe" if sys.platform.startswith("win") else "node")


def _runner_path(config: SignupConfig) -> Path:
    # Prefer .cjs so the CommonJS runner stays valid inside projects whose
    # package.json declares "type": "module".  Keep .js as a fallback for
    # older projects/templates that only ship the original runner name.
    cjs_runner = config.sentinel_dir / "sentinel-runner.cjs"
    if cjs_runner.exists():
        return cjs_runner
    return config.sentinel_dir / "sentinel-runner.js"


def ensure_runner_environment(config: SignupConfig) -> None:
    runner = _runner_path(config)
    sdk = config.sentinel_dir / "sdk.js"
    if not runner.exists():
        raise FileNotFoundError(f"找不到 sentinel-runner.cjs 或 sentinel-runner.js: {runner}")
    if not sdk.exists():
        raise FileNotFoundError(f"找不到 sdk.js: {sdk}")


def _profile_args(profile) -> list[str]:
    width = str(getattr(profile, "screen_width", 1920) if profile else 1920)
    height = str(getattr(profile, "screen_height", 1080) if profile else 1080)
    cores = str(getattr(profile, "hardware_concurrency", 32) if profile else 32)
    language = str(getattr(profile, "language", "en-US") if profile else "en-US")
    languages_value = getattr(profile, "languages", ["en-US", "en"]) if profile else ["en-US", "en"]
    if isinstance(languages_value, str):
        languages = languages_value
    else:
        languages = ",".join(str(item) for item in languages_value)
    heap_limit = str(getattr(profile, "js_heap_size_limit", 4294967296) if profile else 4294967296)
    return [
        "--width", width,
        "--height", height,
        "--cores", cores,
        "--language", language,
        "--languages", languages,
        "--js-heap-size-limit", heap_limit,
    ]


def _validate_token_shape(token: str) -> None:
    if not token:
        raise RuntimeError("sentinel token 为空")
    try:
        parsed = json.loads(token)
    except Exception as exc:
        raise RuntimeError(f"sentinel token 不是合法 JSON（len={len(token)}）") from exc
    for key in ("p", "c", "id", "flow"):
        if key not in parsed:
            raise RuntimeError(f"sentinel token 缺少字段 {key}（len={len(token)}）")


def _run_runner(config: SignupConfig, cmd: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # 强制忽略目录里的 sentinel.config.json，所有参数由调用方显式传入，避免跨项目污染。
    env["SENTINEL_CONFIG"] = "__none__"
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(config.project_root),
        timeout=timeout_seconds,
        env=env,
    )


def _base_cmd(config: SignupConfig, challenge_file: str, flow: str, device_id: str, user_agent: str | None, page_url: str | None, profile) -> list[str]:
    return [
        _node_executable(),
        str(_runner_path(config)),
        "--challenge-file", challenge_file,
        "--flow", flow,
        "--device-id", device_id,
        "--page-url", page_url or _FLOW_PAGE_URL.get(flow, "https://auth.openai.com/create-account/password"),
        "--user-agent", user_agent or config.user_agent,
        "--sdk", str(config.sentinel_dir / "sdk.js"),
        *_profile_args(profile),
        "--no-cookie",
    ]


def generate_sentinel_headers(
    config: SignupConfig,
    challenge: dict,
    flow: str,
    device_id: str,
    user_agent: str | None = None,
    page_url: str | None = None,
    profile=None,
    route: str = "sdk",
    observer_wait_ms: int = _DEFAULT_OBSERVER_WAIT_MS,
) -> SentinelHeaders:
    """Generate Sentinel headers through the bundled SDK runner.

    route="legacy" preserves the old single-header behavior. route="sdk" asks the
    runner for JSON output and returns the SDK-generated session observer token
    as ``so_token`` when the current Sentinel requirements include it.
    """

    ensure_runner_environment(config)
    if not flow:
        raise ValueError("flow 不能为空")
    if not device_id:
        raise ValueError("device_id 不能为空")

    normalized_route = (route or "sdk").strip().lower()
    if normalized_route not in {"legacy", "sdk"}:
        normalized_route = "sdk"
    wait_ms = int(observer_wait_ms if observer_wait_ms is not None else _DEFAULT_OBSERVER_WAIT_MS)
    if wait_ms < 0:
        wait_ms = _DEFAULT_OBSERVER_WAIT_MS

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", prefix=f"sentinel-{flow}-", delete=False, encoding="utf-8")
    try:
        json.dump(challenge, tmp, ensure_ascii=False)
        tmp.flush()
        tmp.close()
        cmd = _base_cmd(config, tmp.name, flow, device_id, user_agent, page_url, profile)
        if normalized_route == "sdk":
            cmd.extend(["--output", "json", "--observer-wait-ms", str(wait_ms)])
        timeout_seconds = max(75, 30 + int(wait_ms / 1000) + 20)
        proc = _run_runner(config, cmd, timeout_seconds=timeout_seconds)
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        if proc.returncode != 0:
            raise RuntimeError(
                f"{Path(cmd[1]).name} 退出码 "
                f"{proc.returncode}; stderr={stderr[:500]}; stdout_len={len(stdout)}"
            )
        if not stdout:
            raise RuntimeError(f"{Path(cmd[1]).name} 输出为空; stderr={stderr[:500]}")

        if normalized_route == "legacy":
            _validate_token_shape(stdout)
            return SentinelHeaders(
                token=stdout,
                so_token=None,
                token_len=len(stdout),
                so_len=0,
                so_present=False,
                route="legacy",
                flow=flow,
                sdk=str(config.sentinel_dir / "sdk.js"),
                observer_wait_ms=0,
            )

        try:
            payload = json.loads(stdout)
        except Exception as exc:
            raise RuntimeError(f"{Path(cmd[1]).name} JSON 输出无法解析（len={len(stdout)}）") from exc
        token = str(payload.get("token") or "").strip()
        so_token = str(payload.get("soToken") or payload.get("so_token") or "").strip() or None
        _validate_token_shape(token)
        headers = SentinelHeaders(
            token=token,
            so_token=so_token,
            token_len=int(payload.get("tokenLen") or len(token)),
            so_len=int(payload.get("soTokenLen") or (len(so_token or ""))),
            so_present=bool(payload.get("soPresent") or so_token),
            route="sdk",
            flow=flow,
            sdk=str(payload.get("sdkVersion") or Path(config.sentinel_dir / "sdk.js").name),
            observer_wait_ms=int(payload.get("observerWaitMS") or wait_ms),
            so_attempted=bool(payload.get("soAttempted")),
            so_init_attempted=bool(payload.get("soInitAttempted")),
            so_error=str(payload.get("soError") or ""),
        )
        logger.info(
            "sentinel headers generated: flow=%s route=%s token_len=%s so_present=%s so_len=%s sdk=%s observer_wait_ms=%s",
            headers.flow,
            headers.route,
            headers.token_len,
            headers.so_present,
            headers.so_len,
            headers.sdk,
            headers.observer_wait_ms,
        )
        return headers
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def generate_sentinel_token(config: SignupConfig, challenge: dict, flow: str, device_id: str, user_agent: str | None = None, page_url: str | None = None, profile=None) -> str:
    # 兼容旧调用点：只返回 OpenAI-Sentinel-Token，不生成/返回 SO header。
    return generate_sentinel_headers(
        config,
        challenge,
        flow,
        device_id,
        user_agent=user_agent,
        page_url=page_url,
        profile=profile,
        route="legacy",
        observer_wait_ms=0,
    ).token
