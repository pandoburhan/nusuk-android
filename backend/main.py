import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import init_db, SessionLocal
from backend.models import BotSetting, LogSeverity
from backend.migrations.v2_migration import run_migration
from backend.orchestrator import orchestrator
from backend.event_log import log_event
from backend.telemetry import telemetry_manager
from backend.time_service import time_service
from backend.auth import require_auth, verify_access_token
from backend.routers import (
    auth, instances, control, keys, analytics, logs, settings as settings_router,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("nusuk_backend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing database & running v2 migration check...")
    init_db()
    mig = run_migration()
    if not mig.get("ok"):
        logger.error(f"Database migration verification issues: {mig.get('problems')}")

    try:
        loop = asyncio.get_running_loop()
        telemetry_manager.set_loop(loop)
    except RuntimeError:
        pass

    db: Session = SessionLocal()
    try:
        for item in db.query(BotSetting).all():
            v = item.setting_value
            if not v:
                continue
            if item.setting_key == "ai_base_url":
                settings.ai_base_url = v
            elif item.setting_key == "ai_api_key":
                settings.ai_api_key = v
            elif item.setting_key == "ai_model":
                settings.ai_model = v
            elif item.setting_key == "loading_timeout_seconds":
                try:
                    settings.loading_timeout_seconds = int(v)
                except ValueError:
                    pass
            elif item.setting_key == "circuit_breaker_limit":
                try:
                    settings.circuit_breaker_limit = int(v)
                except ValueError:
                    pass
            elif item.setting_key == "action_delay_seconds":
                try:
                    settings.action_delay_seconds = float(v)
                except ValueError:
                    pass
            elif item.setting_key == "watchdog_interval_seconds":
                try:
                    settings.watchdog_interval_seconds = int(v)
                except ValueError:
                    pass
            elif item.setting_key == "preview_refresh_seconds":
                try:
                    settings.preview_refresh_seconds = int(v)
                except ValueError:
                    pass
    except Exception as e:
        logger.error(f"Error loading settings: {e}")
    finally:
        db.close()

    time_service.start_background_sync()
    orchestrator.startup()
    log_event(LogSeverity.INFO, "SYSTEM", "Server backend v2.0 Multi-Instance siap.")

    yield

    logger.info("Shutting down Nusuk Backend...")
    orchestrator.shutdown()
    time_service.stop_background_sync()


app = FastAPI(
    title="Nusuk Automation & Key Management Hub",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if not verify_access_token(token):
        await websocket.close(code=4401)
        return
    await telemetry_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await telemetry_manager.disconnect(websocket)
    except Exception as e:
        logger.warning(f"WebSocket error: {e}")
        await telemetry_manager.disconnect(websocket)


app.include_router(auth.router)

_auth_deps = [Depends(require_auth)]
app.include_router(instances.router, dependencies=_auth_deps)
app.include_router(control.router, dependencies=_auth_deps)
app.include_router(keys.router, dependencies=_auth_deps)
app.include_router(analytics.router, dependencies=_auth_deps)
app.include_router(logs.router, dependencies=_auth_deps)
app.include_router(settings_router.router, dependencies=_auth_deps)

frontend_dist = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend", "dist"))
assets_dir = os.path.join(frontend_dist, "assets")
if os.path.exists(assets_dir):
    app.mount("/assets", StaticFiles(directory=assets_dir), name="static_assets")


@app.get("/")
def root():
    index_file = os.path.join(frontend_dist, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {
        "system": "Nusuk Quiz & Key Automation Hub",
        "version": "2.0.0",
        "status": "online",
        "docs_url": "/docs",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=settings.server_host, port=settings.server_port, reload=True)
