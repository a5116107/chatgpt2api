from __future__ import annotations

import base64
import binascii
import io
import math
import re
from dataclasses import dataclass
from typing import Any

from PIL import Image, UnidentifiedImageError

from services.image_storage_service import ImageStorageService, image_storage_service

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
            save_options: dict[str, Any] = {"format": "PNG", "compress_level": 6}
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
        if not original_url:
            original_url = self.storage.save(source_payload, base_url).url

        output_url = original_url
        transform = "original"
        if output != source:
            output_payload = _resize_png(source_payload, output)
            output_url = self.storage.save(output_payload, base_url).url
            transform = "lanczos_upscale"

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
