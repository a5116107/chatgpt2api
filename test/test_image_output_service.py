from __future__ import annotations

import base64
import io
import unittest

from PIL import Image

from services.image_output_service import ImageOutputError, ImageOutputService, parse_target_size


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

    def save(self, payload: bytes, _base_url: str | None = None) -> FakeStoredImage:
        self.payloads.append(payload)
        return FakeStoredImage(f"https://example.test/images/{len(self.payloads)}.png")


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


if __name__ == "__main__":
    unittest.main()
