from __future__ import annotations

from contextlib import asynccontextmanager
from threading import Event

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from api import accounts, ai, aurora, conversations, image_tasks, register, risk, runtime_profiles, system, videos
from api.errors import install_exception_handlers
from api.support import resolve_web_asset, start_image_account_probe, start_limited_account_watcher
from services.backup_service import backup_service
from services.config import config
from services.image_service import start_image_cleanup_scheduler
from services.account_service import account_service


def create_app() -> FastAPI:
    app_version = config.app_version

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        stop_event = Event()
        cleanup = account_service.cleanup_persisted_terminal_accounts("startup_reconcile")
        if cleanup.get("removed") or cleanup.get("quarantined"):
            print(
                "[startup-account-reconcile] "
                f"removed={cleanup.get('removed', 0)} "
                f"quarantined={cleanup.get('quarantined', 0)}"
            )
        thread = start_limited_account_watcher(stop_event)
        image_probe_thread = start_image_account_probe(stop_event)
        cleanup_thread = start_image_cleanup_scheduler(stop_event)
        backup_service.start()
        config.cleanup_old_images()
        account_service.cleanup_orphan_runtime_profiles()
        try:
            yield
        finally:
            stop_event.set()
            thread.join(timeout=1)
            image_probe_thread.join(timeout=1)
            cleanup_thread.join(timeout=1)
            backup_service.stop()

    app = FastAPI(title="chatgpt2api", version=app_version, lifespan=lifespan)
    install_exception_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(ai.create_router())
    app.include_router(aurora.create_router())
    app.include_router(accounts.create_router())
    app.include_router(conversations.create_router())
    app.include_router(image_tasks.create_router())
    app.include_router(register.create_router())
    app.include_router(runtime_profiles.create_router())
    app.include_router(risk.create_router())
    app.include_router(system.create_router(app_version))
    app.include_router(videos.create_router())

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_web(full_path: str):
        asset = resolve_web_asset(full_path)
        if asset is not None:
            return FileResponse(asset)
        if full_path.strip("/").startswith("_next/"):
            raise HTTPException(status_code=404, detail="Not Found")
        fallback = resolve_web_asset("")
        if fallback is None:
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(fallback)

    return app
