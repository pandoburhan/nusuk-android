import os
import re
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.models import BotSetting
from backend.schemas import BotSettingsUpdate, TestAIRequest, ActionResponse
from backend.quiz_solver import quiz_solver

router = APIRouter(prefix="/api/settings", tags=["Settings"])


def persist_setting_to_db(db: Session, key: str, value: str):
    item = db.query(BotSetting).filter(BotSetting.setting_key == key).first()
    if item:
        item.setting_value = value
    else:
        item = BotSetting(setting_key=key, setting_value=value)
        db.add(item)
    db.commit()


def update_env_file(key: str, value: str):
    """Updates key=value in .env if it exists."""
    env_path = os.path.abspath(".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            content = f.read()
        pattern = rf"^{key}=.*$"
        new_line = f"{key}={value}"
        if re.search(pattern, content, flags=re.MULTILINE):
            content = re.sub(pattern, new_line, content, flags=re.MULTILINE)
        else:
            content += f"\n{new_line}"
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        pass


@router.get("")
def get_settings():
    masked_key = ""
    if settings.ai_api_key:
        k = settings.ai_api_key.strip()
        masked_key = k[:4] + "..." + k[-4:] if len(k) > 8 else "***"

    return {
        "ai_base_url": settings.ai_base_url,
        "ai_api_key_masked": masked_key,
        "has_ai_key": bool(settings.ai_api_key),
        "ai_model": settings.ai_model,
        "loading_timeout_seconds": settings.loading_timeout_seconds,
        "circuit_breaker_limit": settings.circuit_breaker_limit,
        "action_delay_seconds": settings.action_delay_seconds,
        "watchdog_interval_seconds": settings.watchdog_interval_seconds,
        "preview_refresh_seconds": settings.preview_refresh_seconds,
        "db_host": settings.db_host,
        "db_port": settings.db_port,
        "db_name": settings.db_name,
    }


@router.post("", response_model=ActionResponse)
def update_settings(payload: BotSettingsUpdate, db: Session = Depends(get_db)):
    if payload.ai_base_url is not None:
        val = payload.ai_base_url.strip()
        settings.ai_base_url = val
        persist_setting_to_db(db, "ai_base_url", val)
        update_env_file("AI_BASE_URL", val)

    if payload.ai_api_key is not None:
        val = payload.ai_api_key.strip()
        settings.ai_api_key = val
        persist_setting_to_db(db, "ai_api_key", val)
        update_env_file("AI_API_KEY", val)

    if payload.ai_model is not None:
        val = payload.ai_model.strip()
        settings.ai_model = val
        persist_setting_to_db(db, "ai_model", val)
        update_env_file("AI_MODEL", val)

    if payload.loading_timeout_seconds is not None:
        settings.loading_timeout_seconds = max(5, int(payload.loading_timeout_seconds))
        persist_setting_to_db(db, "loading_timeout_seconds", str(settings.loading_timeout_seconds))
        update_env_file("LOADING_TIMEOUT_SECONDS", str(settings.loading_timeout_seconds))

    if payload.circuit_breaker_limit is not None:
        settings.circuit_breaker_limit = max(1, int(payload.circuit_breaker_limit))
        persist_setting_to_db(db, "circuit_breaker_limit", str(settings.circuit_breaker_limit))
        update_env_file("CIRCUIT_BREAKER_LIMIT", str(settings.circuit_breaker_limit))

    if payload.action_delay_seconds is not None:
        settings.action_delay_seconds = max(0.0, min(5.0, float(payload.action_delay_seconds)))
        persist_setting_to_db(db, "action_delay_seconds", str(settings.action_delay_seconds))
        update_env_file("ACTION_DELAY_SECONDS", str(settings.action_delay_seconds))

    if payload.watchdog_interval_seconds is not None:
        settings.watchdog_interval_seconds = max(3, min(60, int(payload.watchdog_interval_seconds)))
        persist_setting_to_db(db, "watchdog_interval_seconds", str(settings.watchdog_interval_seconds))
        update_env_file("WATCHDOG_INTERVAL_SECONDS", str(settings.watchdog_interval_seconds))

    if payload.preview_refresh_seconds is not None:
        settings.preview_refresh_seconds = max(2, min(30, int(payload.preview_refresh_seconds)))
        persist_setting_to_db(db, "preview_refresh_seconds", str(settings.preview_refresh_seconds))
        update_env_file("PREVIEW_REFRESH_SECONDS", str(settings.preview_refresh_seconds))

    return ActionResponse(success=True, message="Pengaturan AI dan sistem berhasil disimpan.")


@router.post("/test-ai", response_model=ActionResponse)
def test_ai_connection(payload: TestAIRequest):
    base_url = payload.ai_base_url or settings.ai_base_url
    api_key = payload.ai_api_key if payload.ai_api_key is not None and payload.ai_api_key != "" else settings.ai_api_key
    model = payload.ai_model or settings.ai_model

    result = quiz_solver.test_connection(base_url=base_url, api_key=api_key, model=model)
    if result.get("success"):
        mode_desc = "JSON Mode" if result.get("mode") == "json_object" else "Standard Fallback Mode"
        return ActionResponse(
            success=True,
            message=f"Koneksi AI berhasil! Model '{result.get('model')}' merespons dengan baik ({mode_desc}).",
            data=result,
        )
    raise HTTPException(
        status_code=400,
        detail=f"Koneksi ke AI Provider gagal: {result.get('error')}",
    )
