from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import QuizLog, QuizResultStatus, SystemLog, LogSeverity, Instance

router = APIRouter(prefix="/api/logs", tags=["Logs"])


def _device_lookup(db: Session) -> dict:
    return {r.uuid: (r.name, bool(r.is_deleted)) for r in db.query(Instance).all()}


@router.get("/devices")
def list_log_devices(db: Session = Depends(get_db)):
    rows = db.query(Instance).order_by(Instance.is_deleted.asc(), Instance.created_at.asc()).all()
    return {
        "items": [
            {"uuid": r.uuid, "name": r.name, "is_deleted": bool(r.is_deleted)}
            for r in rows
        ]
    }


@router.get("/quiz")
def list_quiz_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=5, le=100),
    result: Optional[str] = Query(default=None),
    door: Optional[str] = Query(default=None),
    device_uuid: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    devs = _device_lookup(db)
    query = db.query(QuizLog)
    if result and result.lower() != "all":
        try:
            query = query.filter(QuizLog.result_status == QuizResultStatus(result.lower()))
        except ValueError:
            pass
    if door and door.lower() != "all":
        query = query.filter(QuizLog.door_category.ilike(f"%{door}%"))
    if device_uuid and device_uuid.lower() != "all":
        query = query.filter(QuizLog.device_uuid == device_uuid)

    total = query.count()
    items = query.order_by(QuizLog.id.desc()).offset((page - 1) * page_size).limit(page_size).all()

    out = []
    for item in items:
        dname, darch = devs.get(item.device_uuid, (item.device_id, False))
        out.append({
            "id": item.id,
            "device_id": item.device_id,
            "device_uuid": item.device_uuid,
            "device_name": dname,
            "device_archived": darch,
            "mode": item.mode,
            "door_category": item.door_category,
            "question_text": item.question_text,
            "options_json": item.options_json,
            "ai_selected_answer": item.ai_selected_answer,
            "ai_selected_index": item.ai_selected_index,
            "result_status": item.result_status.value,
            "duration_ms": item.duration_ms,
            "ai_duration_ms": item.ai_duration_ms,
            "created_at": item.created_at.isoformat(),
        })

    return {"total": total, "page": page, "page_size": page_size, "items": out}


@router.get("/system")
def list_system_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=200),
    severity: Optional[str] = Query(default=None),
    device_uuid: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    devs = _device_lookup(db)
    query = db.query(SystemLog)
    if severity and severity.upper() != "ALL":
        try:
            query = query.filter(SystemLog.severity == LogSeverity(severity.upper()))
        except ValueError:
            pass
    if device_uuid and device_uuid != "all":
        if device_uuid == "GLOBAL":
            query = query.filter(SystemLog.device_uuid.is_(None))
        else:
            query = query.filter(SystemLog.device_uuid == device_uuid)

    total = query.count()
    items = query.order_by(SystemLog.id.desc()).offset((page - 1) * page_size).limit(page_size).all()

    out = []
    for item in items:
        dname = devs.get(item.device_uuid, (None, False))[0] if item.device_uuid else None
        out.append({
            "id": item.id,
            "device_uuid": item.device_uuid,
            "device_name": dname,
            "severity": item.severity.value,
            "category": item.category,
            "message": item.message,
            "last_screen_state": item.last_screen_state,
            "created_at": item.created_at.isoformat(),
        })

    return {"total": total, "page": page, "page_size": page_size, "items": out}
