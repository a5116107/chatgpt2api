from __future__ import annotations

import base64
import json
import random
import time
import uuid
from datetime import datetime


def _b64(data) -> str:
    return base64.b64encode(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).decode("ascii")


def generate_fingerprint_data(device_id: str, user_agent: str, sentinel_sv: str, attempt: int = 1, elapsed_ms: float = 0) -> list:
    now = datetime.utcnow().strftime("%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)")
    perf_now = random.uniform(1000, 50000)
    time_origin = time.time() * 1000 - perf_now
    return [
        "1920x1080",
        now,
        4294705152,
        attempt,
        user_agent,
        f"https://sentinel.openai.com/sentinel/{sentinel_sv}/sdk.js",
        None,
        None,
        "en-US",
        "en-US,en",
        random.random(),
        random.choice(["vendor-undefined", "hardwareConcurrency-undefined", "plugins-undefined"]),
        random.choice(["location", "implementation", "URL", "documentURI", "compatMode"]),
        random.choice(["Object", "Function", "Array", "Number", "parseFloat", "undefined"]),
        perf_now,
        str(uuid.uuid4()),
        "",
        random.choice([4, 8, 12, 16]),
        time_origin,
    ]


def generate_requirements_token(device_id: str, user_agent: str, sentinel_sv: str) -> str:
    cfg = generate_fingerprint_data(device_id, user_agent, sentinel_sv, attempt=1, elapsed_ms=random.uniform(5, 50))
    return "gAAAAAC" + _b64(cfg) + "~S"


def build_sentinel_request_body(p: str, device_id: str, flow: str) -> str:
    return json.dumps({"p": p, "id": device_id, "flow": flow}, separators=(",", ":"))
