from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from services.image_storage_service import ImageStorageError, ImageStorageService


def png_bytes() -> bytes:
    path = Path(tempfile.gettempdir()) / "chatgpt2api-test-image.png"
    Image.new("RGB", (2, 2), color=(255, 0, 0)).save(path, format="PNG")
    return path.read_bytes()


class FakeWebDAVClient:
    uploaded: dict[str, bytes] = {}
    deleted: list[str] = []
    closed = 0
    put_calls = 0
    fail_on_put = 0

    def __init__(self, _settings):
        pass

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.close()

    def close(self) -> None:
        type(self).closed += 1

    def put(self, rel: str, payload: bytes) -> str:
        type(self).put_calls += 1
        if self.fail_on_put and self.put_calls == self.fail_on_put:
            raise ImageStorageError("simulated WebDAV failure")
        self.uploaded[rel] = payload
        return f"https://dav.example.test/{rel}"

    def get(self, rel: str) -> bytes:
        return self.uploaded[rel]

    def delete(self, rel: str) -> bool:
        self.deleted.append(rel)
        self.uploaded.pop(rel, None)
        return True

    def test(self) -> dict[str, object]:
        self.put(".chatgpt2api_webdav_test.txt", b"chatgpt2api webdav test\n")
        self.delete(".chatgpt2api_webdav_test.txt")
        return {"ok": True, "status": 200, "error": None}


class ImageStorageServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name)
        self.images_dir = self.data_dir / "images"
        self.settings = {
            "enabled": False,
            "mode": "local",
            "webdav_url": "",
            "webdav_username": "",
            "webdav_password": "",
            "webdav_root_path": "chatgpt2api/images",
            "public_base_url": "",
        }
        self.config_patcher = mock.patch("services.image_storage_service.config")
        self.mock_config = self.config_patcher.start()
        self.addCleanup(self.config_patcher.stop)
        self.mock_config.images_dir = self.images_dir
        self.mock_config.base_url = "http://app.test"
        self.mock_config.cleanup_old_images.return_value = 0
        self.mock_config.get_image_storage_settings.side_effect = lambda: dict(self.settings)
        FakeWebDAVClient.uploaded = {}
        FakeWebDAVClient.deleted = []
        FakeWebDAVClient.closed = 0
        FakeWebDAVClient.put_calls = 0
        FakeWebDAVClient.fail_on_put = 0

    def service(self) -> ImageStorageService:
        return ImageStorageService(self.data_dir / "image_index.json")

    def test_local_mode_saves_to_local_directory(self):
        stored = self.service().save(png_bytes(), "http://app.test")

        self.assertEqual(stored.storage, "local")
        self.assertTrue((self.images_dir / stored.rel).is_file())
        self.assertEqual(stored.url, f"http://app.test/images/{stored.rel}")

    def test_identical_payloads_receive_distinct_paths(self):
        service = self.service()
        payload = png_bytes()

        first = service.save(payload, "http://app.test")
        second = service.save(payload, "http://app.test")

        self.assertNotEqual(first.rel, second.rel)
        self.assertTrue((self.images_dir / first.rel).is_file())
        self.assertTrue((self.images_dir / second.rel).is_file())

    def test_save_many_cleans_and_updates_index_once(self):
        service = self.service()
        first = png_bytes()
        second = png_bytes() + b"distinct"
        with mock.patch.object(service, "_save_index", wraps=service._save_index) as save_index:
            stored = service.save_many(
                [(first, (2, 2)), (second, (2, 2))],
                "http://app.test",
            )

        self.assertEqual(len(stored), 2)
        self.assertEqual(self.mock_config.cleanup_old_images.call_count, 1)
        save_index.assert_called_once()
        self.assertTrue(all((self.images_dir / item.rel).is_file() for item in stored))

    def test_save_many_rolls_back_batch_when_later_write_fails(self):
        service = self.service()
        first = png_bytes()
        with self.assertRaisesRegex(ImageStorageError, "image payload is empty"):
            service.save_many([(first, (2, 2)), (b"", None)], "http://app.test")

        self.assertEqual(list(self.images_dir.rglob("*.png")), [])
        self.assertEqual(service._load_index(), {})

    def test_save_many_rolls_back_local_and_webdav_writes(self):
        self.settings.update({
            "enabled": True,
            "mode": "both",
            "webdav_url": "https://dav.example.test",
            "webdav_password": "secret",
        })
        FakeWebDAVClient.fail_on_put = 2
        service = self.service()
        with (
            mock.patch("services.image_storage_service.WebDAVClient", FakeWebDAVClient),
            self.assertRaisesRegex(ImageStorageError, "simulated WebDAV failure"),
        ):
            service.save_many(
                [(png_bytes(), (2, 2)), (png_bytes() + b"distinct", (2, 2))],
                "http://app.test",
            )

        self.assertEqual(list(self.images_dir.rglob("*.png")), [])
        self.assertEqual(FakeWebDAVClient.uploaded, {})
        self.assertEqual(len(FakeWebDAVClient.deleted), 1)
        self.assertEqual(FakeWebDAVClient.closed, 1)
        self.assertEqual(service._load_index(), {})

    def test_webdav_mode_uploads_without_local_file(self):
        self.settings.update({
            "enabled": True,
            "mode": "webdav",
            "webdav_url": "https://dav.example.test",
            "webdav_password": "secret",
        })
        with mock.patch("services.image_storage_service.WebDAVClient", FakeWebDAVClient):
            stored = self.service().save(png_bytes(), "http://app.test")
            payload = self.service().get_bytes(stored.rel)

        self.assertEqual(stored.storage, "webdav")
        self.assertFalse((self.images_dir / stored.rel).exists())
        self.assertIn(stored.rel, FakeWebDAVClient.uploaded)
        self.assertEqual(payload, FakeWebDAVClient.uploaded[stored.rel])
        self.assertEqual(FakeWebDAVClient.closed, 2)

    def test_list_items_ignores_non_image_files(self):
        image = png_bytes()
        image_path = self.images_dir / "2026" / "05" / "07" / "sample.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(image)
        (self.images_dir / ".DS_Store").write_text("not an image", encoding="utf-8")
        (self.images_dir / "2026" / ".DS_Store").write_text("not an image", encoding="utf-8")

        items = self.service().list_items("http://app.test")

        self.assertEqual([item["rel"] for item in items], ["2026/05/07/sample.png"])
        self.assertEqual(items[0]["storage"], "local")

    def test_both_mode_saves_to_local_and_webdav(self):
        self.settings.update({
            "enabled": True,
            "mode": "both",
            "webdav_url": "https://dav.example.test",
            "webdav_password": "secret",
            "public_base_url": "https://cdn.example.test/images",
        })
        with mock.patch("services.image_storage_service.WebDAVClient", FakeWebDAVClient):
            stored = self.service().save(png_bytes(), "http://app.test")

        self.assertEqual(stored.storage, "both")
        self.assertTrue((self.images_dir / stored.rel).is_file())
        self.assertIn(stored.rel, FakeWebDAVClient.uploaded)
        self.assertEqual(stored.url, f"https://cdn.example.test/images/{stored.rel}")

    def test_test_webdav_writes_and_deletes_probe_file(self):
        self.settings.update({
            "enabled": True,
            "mode": "webdav",
            "webdav_url": "https://dav.example.test",
            "webdav_password": "secret",
        })
        with mock.patch("services.image_storage_service.WebDAVClient", FakeWebDAVClient):
            result = self.service().test_webdav()

        self.assertTrue(result["ok"])
        self.assertIn(".chatgpt2api_webdav_test.txt", FakeWebDAVClient.deleted)


if __name__ == "__main__":
    unittest.main()
