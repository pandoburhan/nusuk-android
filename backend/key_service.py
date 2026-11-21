"""
Many-to-many key claim logic (blueprint 2.x). Every function opens its OWN short-lived DB
session, so it is safe to call from any worker thread (never share a Session across threads).
"""
import datetime
import logging
from typing import Optional, Tuple

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from backend.database import SessionLocal
from backend.models import RedeemKey, DeviceKeyClaim, ClaimStatus
from backend.time_service import utc_now_naive

logger = logging.getLogger(__name__)

ELIGIBLE_SQL = (
    "rk.is_blacklisted = 0 AND NOT EXISTS ("
    "SELECT 1 FROM device_key_claims c WHERE c.key_id = rk.id AND c.device_uuid = :d)"
)


def eligible_count(device_uuid: str) -> int:
    db = SessionLocal()
    try:
        return db.execute(text(f"SELECT COUNT(*) FROM redeem_keys rk WHERE {ELIGIBLE_SQL}"),
                          {"d": device_uuid}).scalar() or 0
    finally:
        db.close()


def lock_next_key(device_uuid: str) -> Optional[Tuple[int, int, str]]:
    """
    Atomically claims the oldest eligible master key for this device.
    Returns (claim_id, key_id, code) or None when this device has no eligible stock.
    Uses SELECT ... FOR UPDATE on the master row + unique (key_id, device_uuid) so two threads /
    devices never double-claim, and retries on MySQL deadlock/duplicate.
    """
    for attempt in range(4):
        db = SessionLocal()
        try:
            row = db.execute(text(
                f"SELECT rk.id FROM redeem_keys rk WHERE {ELIGIBLE_SQL} ORDER BY rk.id ASC LIMIT 1"),
                {"d": device_uuid}).first()
            if not row:
                return None
            key_id = row[0]
            # serialize with concurrent blacklist updates on the same master key
            locked = (
                db.query(RedeemKey)
                .filter(RedeemKey.id == key_id, RedeemKey.is_blacklisted == False)  # noqa: E712
                .with_for_update()
                .first()
            )
            if not locked:
                db.rollback()
                continue
            claim = DeviceKeyClaim(key_id=key_id, device_uuid=device_uuid,
                                   status=ClaimStatus.IN_PROGRESS,
                                   created_at=utc_now_naive())
            db.add(claim)
            db.commit()
            return claim.id, key_id, locked.code
        except (IntegrityError, OperationalError) as e:
            db.rollback()
            logger.warning(f"lock_next_key retry {attempt + 1} for {device_uuid}: {e.__class__.__name__}")
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    return None


def complete_claim(claim_id: int, status: ClaimStatus, note: Optional[str] = None,
                   duration_ms: Optional[int] = None) -> Optional[dict]:
    """
    USED   -> claim becomes used (key stays available to other devices).
    INVALID-> claim invalid + master key GLOBALLY blacklisted (explicit app rejection only).
    Returns info dict (code, key_id, blacklisted_now) or None.
    """
    db = SessionLocal()
    try:
        claim = db.query(DeviceKeyClaim).filter(DeviceKeyClaim.id == claim_id).first()
        if not claim:
            return None
        key = db.query(RedeemKey).filter(RedeemKey.id == claim.key_id).with_for_update().first()
        now = utc_now_naive()
        claim.status = status
        claim.error_note = note
        claim.completed_at = now
        if duration_ms is not None:
            claim.duration_ms = duration_ms
        blacklisted_now = False
        if status == ClaimStatus.INVALID and key and not key.is_blacklisted:
            key.is_blacklisted = True
            key.blacklisted_by_device_uuid = claim.device_uuid
            key.blacklist_reason = note
            key.blacklisted_at = now
            blacklisted_now = True
        db.commit()
        return {"code": key.code if key else "?", "key_id": claim.key_id, "blacklisted_now": blacklisted_now}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def rollback_claim(claim_id: int) -> Optional[str]:
    """Safe rollback: delete an in_progress claim so the key is available again for this device."""
    db = SessionLocal()
    try:
        claim = db.query(DeviceKeyClaim).filter(DeviceKeyClaim.id == claim_id).first()
        if claim and claim.status == ClaimStatus.IN_PROGRESS:
            key = db.query(RedeemKey).filter(RedeemKey.id == claim.key_id).first()
            code = key.code if key else None
            db.delete(claim)
            db.commit()
            return code
    except Exception:
        db.rollback()
        logger.error("rollback_claim failed", exc_info=True)
    finally:
        db.close()
    return None


def sweep_orphan_claims() -> int:
    """Startup global safe rollback: remove every dangling in_progress claim."""
    db = SessionLocal()
    try:
        n = db.query(DeviceKeyClaim).filter(DeviceKeyClaim.status == ClaimStatus.IN_PROGRESS).delete()
        db.commit()
        return n
    except Exception:
        db.rollback()
        return 0
    finally:
        db.close()
