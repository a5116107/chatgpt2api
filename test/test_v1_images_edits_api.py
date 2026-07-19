from __future__ import annotations

import base64
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.ai as ai_module
import api.image_inputs as image_inputs_module
from test.utils import current_auth_headers


PNG_BYTES = b"\x89PNG\r\n\x1a\n"
DATA_IMAGE_URL = f"data:image/png;base64,{base64.b64encode(PNG_BYTES).decode('ascii')}"


class ImagesEditsApiTests(unittest.TestCase):
    def setUp(self):
        self.handle_calls = []

        def fake_handle(payload):
            self.handle_calls.append(payload)
            return {"created": 1, "data": [{"b64_json": base64.b64encode(b"out").decode("ascii")}]}

        self.handler_patcher = mock.patch.object(ai_module.openai_v1_image_edit, "handle", fake_handle)
        self.handler_patcher.start()
        self.addCleanup(self.handler_patcher.stop)
        app = FastAPI()
        app.include_router(ai_module.create_router())
        self.client = TestClient(app)

    def test_edit_accepts_json_image_url(self):
        """测试图片编辑接口支持官方 JSON image_url 引用。"""
        response = self.client.post(
            "/v1/images/edits",
            headers=current_auth_headers(),
            json={
                "model": "gpt-image-2",
                "prompt": "edit",
                "images": [{"image_url": DATA_IMAGE_URL}],
                "n": 1,
                "response_format": "b64_json",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.handle_calls), 1)
        payload = self.handle_calls[0]
        self.assertEqual(payload["prompt"], "edit")
        self.assertEqual(payload["n"], 1)
        self.assertEqual(payload["images"], [(PNG_BYTES, "image_url.png", "image/png")])

    def test_edit_rejects_file_id_reference(self):
        """测试图片编辑接口对暂不支持的 file_id 返回明确错误。"""
        response = self.client.post(
            "/v1/images/edits",
            headers=current_auth_headers(),
            json={
                "model": "gpt-image-2",
                "prompt": "edit",
                "images": [{"file_id": "file-abc123"}],
            },
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("file_id image references are not supported", response.text)
        self.assertEqual(self.handle_calls, [])

    def test_image_url_fetch_bypasses_proxy_for_configured_direct_host(self):
        response = mock.Mock(
            status_code=200,
            content=PNG_BYTES,
            headers={"content-type": "image/png", "content-length": str(len(PNG_BYTES))},
        )

        with (
            mock.patch.dict(
                image_inputs_module.config.data,
                {"image_fetch_direct_hosts": ["api.example.test"]},
            ),
            mock.patch.object(
                image_inputs_module.proxy_settings,
                "build_session_kwargs",
                side_effect=AssertionError("direct image host must not use the upstream proxy"),
            ),
            mock.patch.object(image_inputs_module.requests, "get", return_value=response) as fetch,
        ):
            image = image_inputs_module._download_image_url("https://api.example.test/images/source.png")

        self.assertEqual(image, (PNG_BYTES, "source.png", "image/png"))
        self.assertNotIn("proxy", fetch.call_args.kwargs)

    def test_image_url_fetch_keeps_proxy_for_non_direct_host(self):
        response = mock.Mock(
            status_code=200,
            content=PNG_BYTES,
            headers={"content-type": "image/png", "content-length": str(len(PNG_BYTES))},
        )

        with (
            mock.patch.dict(
                image_inputs_module.config.data,
                {"image_fetch_direct_hosts": ["api.example.test"]},
            ),
            mock.patch.object(
                image_inputs_module.proxy_settings,
                "build_session_kwargs",
                return_value={"proxy": "http://proxy.example.test:8080"},
            ),
            mock.patch.object(image_inputs_module.requests, "get", return_value=response) as fetch,
        ):
            image = image_inputs_module._download_image_url("https://cdn.example.test/source.png")

        self.assertEqual(image, (PNG_BYTES, "source.png", "image/png"))
        self.assertEqual(fetch.call_args.kwargs["proxy"], "http://proxy.example.test:8080")


if __name__ == "__main__":
    unittest.main()
