from __future__ import annotations

import base64
import hashlib
import io
import unittest
from unittest import mock

from PIL import Image

from services.config import config
from services.image_output_service import ImageOutputError, ImageOutputService, _resize_png, parse_target_size


def png_bytes(size: tuple[int, int], color: tuple[int, int, int] = (20, 40, 80)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


class FakeStoredImage:
    def __init__(self, url: str):
        self.url = url


class FakeStorage:
    def __init__(self):
        self.payloads: list[bytes] = []
        self.batch_calls = 0

    def save(self, payload: bytes, _base_url: str | None = None) -> FakeStoredImage:
        self.payloads.append(payload)
        return FakeStoredImage(f"https://example.test/images/{len(self.payloads)}.png")

    def save_many(
        self,
        entries: list[tuple[bytes, tuple[int, int] | None]],
        base_url: str | None = None,
    ) -> list[FakeStoredImage]:
        self.batch_calls += 1
        return [self.save(payload, base_url) for payload, _dimensions in entries]


class ImageOutputServiceTests(unittest.TestCase):
    def test_preserves_original_bytes_and_creates_a_separate_upscaled_png(self) -> None:
        source = png_bytes((200, 100))
        storage = FakeStorage()
        service = ImageOutputService(storage)  # type: ignore[arg-type]

        result = service.prepare_web_output(
            {"b64_json": base64.b64encode(source).decode("ascii")},
            "400x400",
            "https://example.test",
        )

        self.assertEqual(storage.payloads[0], source)
        self.assertEqual(storage.batch_calls, 1)
        self.assertEqual(result["original_url"], "https://example.test/images/1.png")
        self.assertEqual(result["url"], "https://example.test/images/2.png")
        self.assertEqual((result["source_width"], result["source_height"]), (200, 100))
        self.assertEqual((result["width"], result["height"]), (400, 200))
        self.assertEqual(result["output_transform"], "lanczos_upscale")
        with Image.open(io.BytesIO(storage.payloads[1])) as output:
            self.assertEqual(output.size, (400, 200))

    def test_never_downsamples_a_larger_source(self) -> None:
        source = png_bytes((300, 200))
        storage = FakeStorage()
        service = ImageOutputService(storage)  # type: ignore[arg-type]

        result = service.prepare_web_output(
            {"b64_json": base64.b64encode(source).decode("ascii"), "url": "https://example.test/original.png"},
            "150x100",
        )

        self.assertEqual(storage.payloads, [])
        self.assertEqual(result["url"], "https://example.test/original.png")
        self.assertEqual((result["width"], result["height"]), (300, 200))
        self.assertEqual(result["output_transform"], "original")

    def test_rejects_dimensions_beyond_the_web_output_limit(self) -> None:
        with self.assertRaisesRegex(ImageOutputError, "4096"):
            parse_target_size("5000x1000")

    def test_accepts_4k_landscape_within_the_pixel_limit(self) -> None:
        target = parse_target_size("3840x2160")

        self.assertIsNotNone(target)
        self.assertEqual((target.width, target.height), (3840, 2160))

    def test_fast_png_compression_preserves_decoded_pixels(self) -> None:
        source = png_bytes((96, 64), (12, 90, 140))
        with mock.patch.dict(config.data, {"image_png_compress_level": 1}):
            fast = _resize_png(source, parse_target_size("256x256"))  # type: ignore[arg-type]
        with mock.patch.dict(config.data, {"image_png_compress_level": 6}):
            compact = _resize_png(source, parse_target_size("256x256"))  # type: ignore[arg-type]

        with Image.open(io.BytesIO(fast)) as fast_image, Image.open(io.BytesIO(compact)) as compact_image:
            self.assertEqual(fast_image.size, compact_image.size)
            self.assertEqual(fast_image.tobytes(), compact_image.tobytes())

    def test_real_15k_source_is_preserved_for_2k_and_4k_outputs(self) -> None:
        source = png_bytes((1536, 1024), (24, 96, 168))
        source_hash = hashlib.sha256(source).hexdigest()

        for requested_size, expected_size in (
            ("auto", (1536, 1024)),
            ("2048x1365", (2048, 1365)),
            ("4096x2731", (4096, 2731)),
        ):
            with self.subTest(requested_size=requested_size):
                storage = FakeStorage()
                result = ImageOutputService(storage).prepare_web_output(  # type: ignore[arg-type]
                    {"b64_json": base64.b64encode(source).decode("ascii")},
                    requested_size,
                    "https://example.test",
                )

                self.assertEqual(hashlib.sha256(storage.payloads[0]).hexdigest(), source_hash)
                self.assertEqual((result["source_width"], result["source_height"]), (1536, 1024))
                self.assertEqual((result["width"], result["height"]), expected_size)
                with Image.open(io.BytesIO(storage.payloads[-1])) as output:
                    self.assertEqual(output.size, expected_size)


if __name__ == "__main__":
    unittest.main()
