"""DB + WebSocket logging helpers. Every call uses its own short-lived session (thread-safe)."""
import datetime
import logging
from typing import Optional

from backend.database import SessionLocal
from backend.models import SystemLog, LogSeverity, Instance
from backend.telemetry import telemetry_manager
from backend.time_service import utc_now_naive

logger = logging.getLogger(__name__)

_name_cache: dict = {}


def set_device_name(device_uuid: str, name: str):
    _name_cache[device_uuid] = name


def device_name(device_uuid: Optional[str]) -> Optional[str]:
    if not device_uuid:
        return None
    if device_uuid in _name_cache:
        return _name_cache[device_uuid]
    db = SessionLocal()
    try:
        inst = db.query(Instance).filter(Instance.uuid == device_uuid).first()
        if inst:
            _name_cache[device_uuid] = inst.name
            return inst.name
    except Exception:
        pass
    finally:
        db.close()
    return None


def log_event(severity: LogSeverity, category: str, message: str,
              device_uuid: Optional[str] = None, last_screen: Optional[str] = None):
    """Persist a system log row (device_uuid=None -> GLOBAL) and stream it over WebSocket."""
    logger.log(
        {"INFO": logging.INFO, "WARNING": logging.WARNING}.get(severity.value, logging.ERROR),
        f"[{category}] [{(device_name(device_uuid) or 'GLOBAL')}] {message}",
    )
    db = SessionLocal()
    try:
        entry = SystemLog(severity=severity, category=category, message=message,
                          last_screen_state=last_screen, device_uuid=device_uuid,
                          created_at=utc_now_naive())
        db.add(entry)
        db.commit()
        db.refresh(entry)
        telemetry_manager.emit_sync("log_event", {
            "id": entry.id,
            "device_uuid": device_uuid,
            "device_name": device_name(device_uuid),
            "severity": entry.severity.value,
            "category": entry.category,
            "message": entry.message,
            "last_screen_state": entry.last_screen_state,
            "created_at": entry.created_at.isoformat(),
        })
    except Exception as e:
        logger.error(f"Failed to record system log: {e}")
        db.rollback()
    finally:
        db.close()
