import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.schemas import ActionResponse, BulkStartRequest, BulkStopRequest, BulkRestartRequest
from backend.instance_manager import instance_manager
from backend.orchestrator import orchestrator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/control", tags=["Control"])


@router.get("/status")
def get_system_status(db: Session = Depends(get_db)):
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    snap = instance_manager.snapshot()
    counts = {k: 0 for k in ("OFFLINE", "IDLE", "RUNNING", "RECOVERING", "STOPPING", "REBOOTING", "ERROR")}
    for item in snap:
        st = item.get("status", "OFFLINE")
        counts[st] = counts.get(st, 0) + 1

    online = sum(v for k, v in counts.items() if k not in ("OFFLINE", "ERROR"))
    return {
        "database_connected": db_ok,
        "has_ai_key": bool(settings.ai_api_key),
        "total": len(snap),
        "online": online,
        "offline": counts["OFFLINE"],
        "running": counts["RUNNING"],
        "idle": counts["IDLE"],
        "recovering": counts["RECOVERING"],
        "rebooting": counts["REBOOTING"],
        "error": counts["ERROR"],
        "stopping": counts["STOPPING"],
    }


@router.post("/bulk/start")
def bulk_start(req: BulkStartRequest):
    try:
        return orchestrator.start_many(
            device_uuids=req.device_uuids,
            mode=req.mode,
            device_stop_conditions=req.device_stop_conditions,
            stop_mode=req.stop_mode or "limit",
            stop_limit=req.stop_limit or 100,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/bulk/stop")
def bulk_stop(req: BulkStopRequest):
    return orchestrator.stop_many(req.device_uuids)


@router.post("/bulk/force-restart")
def bulk_force_restart(req: BulkRestartRequest):
    try:
        return orchestrator.restart_many(req.device_uuids, req.kind)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/reconnect-all", response_model=ActionResponse)
def reconnect_all_offline():
    n = instance_manager.reconnect_all_offline()
    return ActionResponse(
        success=True,
        message=f"Pemicu reconnect segera dikirim ke {n} device offline." if n else "Tidak ada device offline yang perlu di-reconnect.",
    )
