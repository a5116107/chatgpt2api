from __future__ import annotations

import hashlib
import io
import json
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from urllib.parse import quote, urlparse

from curl_cffi import requests
from fastapi import HTTPException
from PIL import Image

from services.config import DATA_DIR, config

IMAGE_INDEX_FILE = DATA_DIR / "image_index.json"
IMAGE_INDEX_LOCK = Lock()
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
IMAGE_CLEANUP_INTERVAL_SECS = 300.0


class ImageStorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredImage:
    rel: str
    url: str
    storage: str
    size: int


def _clean(value: object) -> str:
    return str(value or "").strip()


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_relative_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/").lstrip("/")
    if not value:
        raise HTTPException(status_code=404, detail="image not found")
    parts = Path(value).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(status_code=404, detail="image not found")
    return Path(*parts).as_posix()


def _image_dimensions(payload: bytes) -> tuple[int, int] | None:
    try:
        with Image.open(io.BytesIO(payload)) as image:
            return image.size
    except Exception:
        return None


def _is_image_rel(path: str) -> bool:
    try:
        safe_rel = _safe_relative_path(path)
    except HTTPException:
        return False
    return Path(safe_rel).suffix.lower() in IMAGE_EXTENSIONS


def _local_image_path(relative_path: str) -> Path:
    rel = _safe_relative_path(relative_path)
    root = config.images_dir.resolve()
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="image not found") from exc
    return path


def _read_json_object(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_object(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


class WebDAVClient:
    def __init__(self, settings: dict[str, object]):
        self.url = _clean(settings.get("webdav_url")).rstrip("/")
        self.username = _clean(settings.get("webdav_username"))
        self.password = _clean(settings.get("webdav_password"))
        self.root_path = _clean(settings.get("webdav_root_path")).strip("/")
        self.session = requests.Session()

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> WebDAVClient:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def _auth_kwargs(self) -> dict[str, object]:
        return {"auth": (self.username, self.password)} if self.username or self.password else {}

    def _request(self, method: str, url: str, **kwargs):
        response = self.session.request(method, url, timeout=30, **self._auth_kwargs(), **kwargs)
        if response.status_code >= 400 and not (method == "MKCOL" and response.status_code in {405}):
            raise ImageStorageError(f"WebDAV {method} failed: HTTP {response.status_code}")
        return response

    def remote_url(self, rel: str = "") -> str:
        parts = [part for part in [self.root_path, _safe_relative_path(rel) if rel else ""] if part]
        encoded = "/".join(quote(part, safe="") for item in parts for part in item.split("/") if part)
        return f"{self.url}/{encoded}" if encoded else self.url

    def ensure_dirs(self, rel: str) -> None:
        parts = [part for part in [self.root_path, Path(_safe_relative_path(rel)).parent.as_posix()] if part and part != "."]
        current = self.url
        for item in "/".join(parts).split("/"):
            if not item:
                continue
            current = f"{current}/{quote(item, safe='')}"
            response = self.session.request("MKCOL", current, timeout=30, **self._auth_kwargs())
            if response.status_code in {201, 405}:
                continue
            if response.status_code >= 400:
                raise ImageStorageError(f"WebDAV MKCOL failed: HTTP {response.status_code}")

    def put(self, rel: str, payload: bytes, content_type: str = "image/png") -> str:
        self.ensure_dirs(rel)
        url = self.remote_url(rel)
        self._request("PUT", url, data=payload, headers={"Content-Type": content_type})
        return url

    def get(self, rel: str) -> bytes:
        response = self._request("GET", self.remote_url(rel))
        return bytes(response.content)

    def delete(self, rel: str) -> bool:
        response = self.session.request("DELETE", self.remote_url(rel), timeout=30, **self._auth_kwargs())
        if response.status_code in {200, 202, 204, 404}:
            return response.status_code != 404
        raise ImageStorageError(f"WebDAV DELETE failed: HTTP {response.status_code}")

    def test(self) -> dict[str, object]:
        if not self.url:
            return {"ok": False, "status": 0, "error": "WebDAV URL is required"}
        if urlparse(self.url).scheme not in {"http", "https"}:
            return {"ok": False, "status": 0, "error": "invalid WebDAV URL"}
        test_rel = ".chatgpt2api_webdav_test.txt"
        try:
            self.put(test_rel, b"chatgpt2api webdav test\n", content_type="text/plain")
            self.delete(test_rel)
            return {"ok": True, "status": 200, "error": None}
        except ImageStorageError as exc:
            return {"ok": False, "status": 0, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "status": 0, "error": str(exc) or exc.__class__.__name__}
        finally:
            self.session.close()


class ImageStorageService:
    def __init__(self, index_file: Path = IMAGE_INDEX_FILE):
        self.index_file = index_file
        self._index_lock = IMAGE_INDEX_LOCK
        self._cleanup_lock = Lock()
        self._last_cleanup_monotonic = 0.0

    def settings(self) -> dict[str, object]:
        return config.get_image_storage_settings()

    def mode(self) -> str:
        return _clean(self.settings().get("mode")) or "local"

    def _load_index(self) -> dict[str, dict[str, object]]:
        raw = _read_json_object(self.index_file)
        items = raw.get("items")
        if not isinstance(items, dict):
            return {}
        return {str(key): value for key, value in items.items() if isinstance(value, dict)}

    def _load_clean_index(self) -> dict[str, dict[str, object]]:
        items = self._load_index()
        return {rel: item for rel, item in items.items() if _is_image_rel(rel)}

    def _save_index(self, items: dict[str, dict[str, object]]) -> None:
        _write_json_object(self.index_file, {"items": items})

    def _public_url(self, rel: str, base_url: str | None = None) -> str:
        settings = self.settings()
        public_base_url = _clean(settings.get("public_base_url"))
        if public_base_url:
            return f"{public_base_url.rstrip('/')}/{_safe_relative_path(rel)}"
        return f"{(base_url or config.base_url).rstrip('/')}/images/{_safe_relative_path(rel)}"

    def make_relative_path(self, image_data: bytes) -> str:
        file_hash = hashlib.md5(image_data).hexdigest()
        filename = f"{time.time_ns()}_{file_hash}.png"
        relative_dir = Path(time.strftime("%Y"), time.strftime("%m"), time.strftime("%d"))
        return f"{relative_dir.as_posix()}/{filename}"

    def _cleanup_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup_monotonic < IMAGE_CLEANUP_INTERVAL_SECS:
            return
        with self._cleanup_lock:
            now = time.monotonic()
            if now - self._last_cleanup_monotonic < IMAGE_CLEANUP_INTERVAL_SECS:
                return
            config.cleanup_old_images()
            self._last_cleanup_monotonic = now

    def save_many(
        self,
        entries: list[tuple[bytes, tuple[int, int] | None]],
        base_url: str | None = None,
    ) -> list[StoredImage]:
        if not entries:
            return []
        self._cleanup_if_due()
        settings = self.settings()
        mode = _clean(settings.get("mode")) or "local"
        if mode not in {"local", "webdav", "both"}:
            mode = "local"
        webdav = WebDAVClient(settings) if mode in {"webdav", "both"} else None
        pending_items: list[dict[str, object]] = []
        stored_images: list[StoredImage] = []
        local_paths: list[Path] = []
        remote_rels: list[str] = []

        try:
            for image_data, supplied_dimensions in entries:
                if not image_data:
                    raise ImageStorageError("image payload is empty")
                rel = self.make_relative_path(image_data)
                stored_local = False
                stored_webdav = False
                remote_url = ""

                if mode in {"local", "both"}:
                    path = _local_image_path(rel)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(image_data)
                    local_paths.append(path)
                    stored_local = True

                if webdav is not None:
                    remote_url = webdav.put(rel, image_data)
                    remote_rels.append(rel)
                    stored_webdav = True

                dimensions = supplied_dimensions or _image_dimensions(image_data)
                storage = "both" if stored_local and stored_webdav else ("webdav" if stored_webdav else "local")
                item: dict[str, object] = {
                    "rel": rel,
                    "path": rel,
                    "name": Path(rel).name,
                    "date": "-".join(rel.split("/")[:3]),
                    "size": len(image_data),
                    "created_at": _now_iso(),
                    "storage": storage,
                    "local": stored_local,
                    "webdav": stored_webdav,
                    "remote_url": remote_url,
                }
                if dimensions:
                    item["width"], item["height"] = dimensions
                pending_items.append(item)
                stored_images.append(
                    StoredImage(
                        rel=rel,
                        url=self._public_url(rel, base_url),
                        storage=storage,
                        size=len(image_data),
                    )
                )

            with self._index_lock:
                items = self._load_clean_index()
                for item in pending_items:
                    items[str(item["rel"])] = item
                self._save_index(items)
            return stored_images
        except Exception:
            for path in reversed(local_paths):
                with suppress(OSError):
                    path.unlink(missing_ok=True)
            if webdav is not None:
                for rel in reversed(remote_rels):
                    with suppress(Exception):
                        webdav.delete(rel)
            raise
        finally:
            close = getattr(webdav, "close", None)
            if callable(close):
                close()

    def save(self, image_data: bytes, base_url: str | None = None) -> StoredImage:
        return self.save_many([(image_data, None)], base_url)[0]

    def get_bytes(self, rel: str) -> bytes:
        safe_rel = _safe_relative_path(rel)
        if not _is_image_rel(safe_rel):
            raise HTTPException(status_code=404, detail="image not found")
        path = _local_image_path(safe_rel)
        if path.is_file():
            return path.read_bytes()
        item = self._load_clean_index().get(safe_rel, {})
        if item.get("webdav"):
            with WebDAVClient(self.settings()) as client:
                return client.get(safe_rel)
        raise HTTPException(status_code=404, detail="image not found")

    def exists(self, rel: str) -> bool:
        safe_rel = _safe_relative_path(rel)
        if not _is_image_rel(safe_rel):
            return False
        if _local_image_path(safe_rel).is_file():
            return True
        item = self._load_clean_index().get(safe_rel, {})
        return bool(item.get("webdav"))

    def has_local(self, rel: str) -> bool:
        safe_rel = _safe_relative_path(rel)
        return _is_image_rel(safe_rel) and _local_image_path(safe_rel).is_file()

    def list_items(self, base_url: str, start_date: str = "", end_date: str = "") -> list[dict[str, object]]:
        with self._index_lock:
            indexed = self._load_clean_index()
            root = config.images_dir
            changed = False
            for path in root.rglob("*"):
                if not path.is_file() or not _is_image_rel(path.name):
                    continue
                rel = path.relative_to(root).as_posix()
                if rel in indexed:
                    continue
                dimensions = None
                try:
                    dimensions = _image_dimensions(path.read_bytes())
                except Exception:
                    dimensions = None
                indexed[rel] = {
                    "rel": rel,
                    "path": rel,
                    "name": path.name,
                    "date": "-".join(rel.split("/")[:3]) if len(rel.split("/")) >= 4 else datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d"),
                    "size": path.stat().st_size,
                    "created_at": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "storage": "local",
                    "local": True,
                    "webdav": False,
                    **({"width": dimensions[0], "height": dimensions[1]} if dimensions else {}),
                }
                changed = True

            items: list[dict[str, object]] = []
            for rel, item in list(indexed.items()):
                if not _is_image_rel(rel):
                    indexed.pop(rel, None)
                    changed = True
                    continue
                local = _local_image_path(rel).is_file()
                webdav = bool(item.get("webdav"))
                if not local and not webdav:
                    indexed.pop(rel, None)
                    changed = True
                    continue
                storage = "both" if local and webdav else ("webdav" if webdav else "local")
                if item.get("local") != local or item.get("storage") != storage:
                    item = {
                        **item,
                        "local": local,
                        "storage": storage,
                    }
                    indexed[rel] = item
                    changed = True
                day = str(item.get("date") or "")
                if start_date and day < start_date:
                    continue
                if end_date and day > end_date:
                    continue
                items.append({
                    **item,
                    "rel": rel,
                    "path": rel,
                    "url": self._public_url(rel, base_url),
                })
            if changed:
                self._save_index(indexed)
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return items

    def delete(self, rel: str) -> bool:
        safe_rel = _safe_relative_path(rel)
        removed = False
        path = _local_image_path(safe_rel)
        if path.is_file():
            path.unlink()
            removed = True
        with self._index_lock:
            items = self._load_clean_index()
            item = items.get(safe_rel, {})
            if item.get("webdav"):
                try:
                    with WebDAVClient(self.settings()) as client:
                        removed = client.delete(safe_rel) or removed
                except ImageStorageError:
                    if not removed:
                        raise
            if safe_rel in items:
                items.pop(safe_rel, None)
                self._save_index(items)
        return removed

    def sync_all(self) -> dict[str, int]:
        settings = self.settings()
        if self.mode() not in {"webdav", "both"}:
            raise ImageStorageError("WebDAV 图片存储未启用")
        uploaded = 0
        skipped = 0
        failed = 0
        with self._index_lock:
            items = self._load_clean_index()
            with WebDAVClient(settings) as client:
                for path in sorted(config.images_dir.rglob("*")):
                    if not path.is_file() or not _is_image_rel(path.name):
                        continue
                    rel = path.relative_to(config.images_dir).as_posix()
                    item = items.get(rel, {})
                    if item.get("webdav"):
                        skipped += 1
                        continue
                    try:
                        payload = path.read_bytes()
                        remote_url = client.put(rel, payload)
                        dimensions = _image_dimensions(payload)
                        items[rel] = {
                            **item,
                            "rel": rel,
                            "path": rel,
                            "name": path.name,
                            "date": "-".join(rel.split("/")[:3]) if len(rel.split("/")) >= 4 else datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d"),
                            "size": len(payload),
                            "created_at": str(item.get("created_at") or datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")),
                            "storage": "both",
                            "local": True,
                            "webdav": True,
                            "remote_url": remote_url,
                            **({"width": dimensions[0], "height": dimensions[1]} if dimensions else {}),
                        }
                        uploaded += 1
                    except Exception:
                        failed += 1
            self._save_index(items)
        return {"uploaded": uploaded, "skipped": skipped, "failed": failed}

    def test_webdav(self) -> dict[str, object]:
        with WebDAVClient(self.settings()) as client:
            return client.test()


image_storage_service = ImageStorageService()
