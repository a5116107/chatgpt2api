from __future__ import annotations

import base64
import binascii
import io
import math
import re
import time
from dataclasses import dataclass
from typing import Any

from PIL import Image, UnidentifiedImageError

from services.config import config
from services.image_storage_service import ImageStorageService, image_storage_service
from utils.log import logger

MAX_OUTPUT_EDGE = 4096
MAX_OUTPUT_PIXELS = 4096 * 4096
SIZE_PATTERN = re.compile(r"^\s*(\d{1,5})\s*[xX]\s*(\d{1,5})\s*$")


class ImageOutputError(ValueError):
    pass


@dataclass(frozen=True)
class TargetSize:
    width: int
    height: int


def parse_target_size(value: object) -> TargetSize | None:
    candidate = str(value or "").strip().lower()
    if not candidate or candidate == "auto":
        return None
    matched = SIZE_PATTERN.fullmatch(candidate)
    if not matched:
        raise ImageOutputError("size must use WIDTHxHEIGHT, for example 1536x1024")
    width, height = (int(part) for part in matched.groups())
    if width < 1 or height < 1:
        raise ImageOutputError("size dimensions must be positive")
    if width > MAX_OUTPUT_EDGE or height > MAX_OUTPUT_EDGE:
        raise ImageOutputError(f"size edge must not exceed {MAX_OUTPUT_EDGE}px")
    if width * height > MAX_OUTPUT_PIXELS:
        raise ImageOutputError(f"size must not exceed {MAX_OUTPUT_PIXELS} pixels")
    return TargetSize(width=width, height=height)


def _decode_image(item: dict[str, Any]) -> bytes:
    encoded = str(item.get("b64_json") or "").strip()
    if not encoded:
        raise ImageOutputError("web image task did not return source bytes")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageOutputError("web image task returned invalid base64 data") from exc
    if not payload:
        raise ImageOutputError("web image task returned an empty image")
    return payload


def _output_dimensions(source: TargetSize, target: TargetSize | None) -> TargetSize:
    if target is None:
        return source
    scale = min(target.width / source.width, target.height / source.height)
    if scale <= 1.0:
        return source
    width = min(MAX_OUTPUT_EDGE, max(source.width, int(round(source.width * scale))))
    height = min(MAX_OUTPUT_EDGE, max(source.height, int(round(source.height * scale))))
    if width * height > MAX_OUTPUT_PIXELS:
        scale = math.sqrt(MAX_OUTPUT_PIXELS / (source.width * source.height))
        width = max(source.width, int(math.floor(source.width * scale)))
        height = max(source.height, int(math.floor(source.height * scale)))
    return TargetSize(width=width, height=height)


def _resize_png(payload: bytes, output_size: TargetSize) -> bytes:
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            if image.mode not in {"1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"}:
                image = image.convert("RGB")
            resized = image.resize((output_size.width, output_size.height), Image.Resampling.LANCZOS)
            save_options: dict[str, Any] = {
                "format": "PNG",
                "compress_level": config.image_png_compress_level,
            }
            icc_profile = image.info.get("icc_profile")
            if isinstance(icc_profile, bytes) and icc_profile:
                save_options["icc_profile"] = icc_profile
            output = io.BytesIO()
            resized.save(output, **save_options)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageOutputError("web image task returned an unreadable image") from exc


class ImageOutputService:
    def __init__(self, storage: ImageStorageService = image_storage_service):
        self.storage = storage

    def prepare_web_output(
        self,
        item: dict[str, Any],
        requested_size: object,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        target = parse_target_size(requested_size)
        source_payload = _decode_image(item)
        try:
            with Image.open(io.BytesIO(source_payload)) as image:
                source_width, source_height = image.size
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ImageOutputError("web image task returned an unreadable image") from exc

        source = TargetSize(source_width, source_height)
        output = _output_dimensions(source, target)
        original_url = str(item.get("url") or "").strip()
        resize_started = time.perf_counter()
        output_payload: bytes | None = None
        transform = "original"
        if output != source:
            output_payload = _resize_png(source_payload, output)
            transform = "lanczos_upscale"
        resize_ms = int((time.perf_counter() - resize_started) * 1000)

        entries: list[tuple[bytes, tuple[int, int] | None]] = []
        original_index: int | None = None
        output_index: int | None = None
        if not original_url:
            original_index = len(entries)
            entries.append((source_payload, (source.width, source.height)))
        if output_payload is not None:
            output_index = len(entries)
            entries.append((output_payload, (output.width, output.height)))

        storage_started = time.perf_counter()
        if entries:
            save_many = getattr(self.storage, "save_many", None)
            if callable(save_many):
                stored = save_many(entries, base_url)
            else:
                # Keep the output service compatible with lightweight storage
                # adapters while the built-in service uses one batched write.
                stored = [self.storage.save(payload, base_url) for payload, _ in entries]
        else:
            stored = []
        storage_ms = int((time.perf_counter() - storage_started) * 1000)
        if original_index is not None:
            original_url = stored[original_index].url
        output_url = stored[output_index].url if output_index is not None else original_url
        logger.info({
            "event": "web_image_output_ready",
            "source_width": source.width,
            "source_height": source.height,
            "output_width": output.width,
            "output_height": output.height,
            "transform": transform,
            "compress_level": config.image_png_compress_level,
            "resize_ms": resize_ms,
            "storage_ms": storage_ms,
            "total_ms": int((time.perf_counter() - started) * 1000),
            "source_bytes": len(source_payload),
            "output_bytes": len(output_payload) if output_payload is not None else len(source_payload),
        })

        return {
            "url": output_url,
            "original_url": original_url,
            "revised_prompt": str(item.get("revised_prompt") or ""),
            "source_width": source.width,
            "source_height": source.height,
            "width": output.width,
            "height": output.height,
            "requested_width": target.width if target else None,
            "requested_height": target.height if target else None,
            "output_transform": transform,
        }


image_output_service = ImageOutputService()
