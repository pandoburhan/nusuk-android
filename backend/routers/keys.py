import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session
from sqlalchemy import text, func, or_, exists, and_
from sqlalchemy.dialects.mysql import insert as mysql_insert

from backend.database import get_db
from backend.models import RedeemKey, DeviceKeyClaim, Instance, KeySource, ClaimStatus
from backend.time_service import utc_now_naive
from backend.schemas import KeyCreateManual, KeyCreateBulk, KeyBulkImportResult, ActionResponse

router = APIRouter(prefix="/api/keys", tags=["Keys"])


def _name_map(db: Session) -> dict:
    return {r.uuid: (r.name, bool(r.is_deleted)) for r in db.query(Instance).all()}


def _master_summary(db: Session) -> dict:
    row = db.execute(text(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN rk.is_blacklisted=1 THEN 1 ELSE 0 END) AS blacklisted, "
        "SUM(CASE WHEN rk.is_blacklisted=0 AND EXISTS ("
        "  SELECT 1 FROM device_key_claims c WHERE c.key_id=rk.id AND c.status='USED'"
        ") THEN 1 ELSE 0 END) AS claimed "
        "FROM redeem_keys rk"
    )).first()
    total = int(row[0] or 0)
    blacklisted = int(row[1] or 0)
    claimed = int(row[2] or 0)
    return {
        "total": total,
        "unused_pure": max(0, total - blacklisted - claimed),
        "claimed": claimed,
        "blacklisted": blacklisted,
    }


@router.get("")
def list_keys(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=5, le=200),
    status: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    device_uuid: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    names = _name_map(db)
    total_devices = db.query(Instance).filter(Instance.is_deleted == False).count()  # noqa: E712
    query = db.query(RedeemKey)

    has_used_claim = exists().where(
        and_(DeviceKeyClaim.key_id == RedeemKey.id, DeviceKeyClaim.status == ClaimStatus.USED)
    )

    if device_uuid:
        has_dev_claim = lambda st: exists().where(
            and_(
                DeviceKeyClaim.key_id == RedeemKey.id,
                DeviceKeyClaim.device_uuid == device_uuid,
                DeviceKeyClaim.status == st,
            )
        )
        has_any_dev_claim = exists().where(
            and_(DeviceKeyClaim.key_id == RedeemKey.id, DeviceKeyClaim.device_uuid == device_uuid)
        )
        st = (status or "all").lower()
        if st == "available":
            query = query.filter(RedeemKey.is_blacklisted == False, ~has_any_dev_claim)  # noqa: E712
        elif st == "in_progress":
            query = query.filter(has_dev_claim(ClaimStatus.IN_PROGRESS))
        elif st == "used":
            query = query.filter(has_dev_claim(ClaimStatus.USED))
        elif st == "invalid":
            query = query.filter(
                or_(
                    has_dev_claim(ClaimStatus.INVALID),
                    and_(RedeemKey.is_blacklisted == True, ~has_dev_claim(ClaimStatus.USED)),  # noqa: E712
                )
            )
    else:
        st = (status or "all").lower()
        if st == "unused_pure":
            query = query.filter(RedeemKey.is_blacklisted == False, ~has_used_claim)  # noqa: E712
        elif st == "claimed":
            query = query.filter(RedeemKey.is_blacklisted == False, has_used_claim)  # noqa: E712
        elif st == "blacklisted":
            query = query.filter(RedeemKey.is_blacklisted == True)  # noqa: E712

    if search and search.strip():
        s = f"%{search.strip()}%"
        search_conds = [RedeemKey.code.ilike(s), RedeemKey.blacklist_reason.ilike(s)]
        if device_uuid:
            search_conds.append(
                exists().where(
                    and_(
                        DeviceKeyClaim.key_id == RedeemKey.id,
                        DeviceKeyClaim.device_uuid == device_uuid,
                        DeviceKeyClaim.error_note.ilike(s),
                    )
                )
            )
        query = query.filter(or_(*search_conds))

    total = query.count()
    keys = query.order_by(RedeemKey.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    key_ids = [k.id for k in keys]

    used_counts = {}
    dev_claims = {}
    if key_ids:
        uc_rows = (
            db.query(DeviceKeyClaim.key_id, func.count(DeviceKeyClaim.id))
            .filter(DeviceKeyClaim.key_id.in_(key_ids), DeviceKeyClaim.status == ClaimStatus.USED)
            .group_by(DeviceKeyClaim.key_id)
            .all()
        )
        used_counts = {kid: int(cnt) for kid, cnt in uc_rows}

        if device_uuid:
            dc_rows = (
                db.query(DeviceKeyClaim)
                .filter(DeviceKeyClaim.key_id.in_(key_ids), DeviceKeyClaim.device_uuid == device_uuid)
                .all()
            )
            dev_claims = {c.key_id: c for c in dc_rows}

    items = []
    for k in keys:
        bl_name = names.get(k.blacklisted_by_device_uuid, (None, False))[0] if k.blacklisted_by_device_uuid else None
        d_status = None
        d_dur = None
        d_note = None
        if device_uuid:
            c = dev_claims.get(k.id)
            if c:
                d_status = c.status.value
                d_dur = c.duration_ms
                d_note = c.error_note
            elif k.is_blacklisted:
                d_status = "invalid"
                d_note = k.blacklist_reason or (f"Blacklisted oleh {bl_name}" if bl_name else "Blacklisted global")
            else:
                d_status = "available"

        items.append({
            "id": k.id,
            "code": k.code,
            "source": k.source.value,
            "created_at": k.created_at.isoformat(),
            "is_blacklisted": bool(k.is_blacklisted),
            "blacklisted_by_device_uuid": k.blacklisted_by_device_uuid,
            "blacklisted_by_name": bl_name,
            "blacklist_reason": k.blacklist_reason,
            "blacklisted_at": k.blacklisted_at.isoformat() if k.blacklisted_at else None,
            "used_count": used_counts.get(k.id, 0),
            "total_devices": total_devices,
            "device_status": d_status,
            "device_duration_ms": d_dur,
            "device_error_note": d_note,
        })

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
        "summary": _master_summary(db),
    }


@router.get("/{key_id}/claims")
def get_key_claims(key_id: int, db: Session = Depends(get_db)):
    k = db.query(RedeemKey).filter(RedeemKey.id == key_id).first()
    if not k:
        raise HTTPException(status_code=404, detail="Key not found.")
    names = _name_map(db)
    total_devices = db.query(Instance).filter(Instance.is_deleted == False).count()  # noqa: E712
    claims = db.query(DeviceKeyClaim).filter(DeviceKeyClaim.key_id == key_id).order_by(DeviceKeyClaim.created_at.asc()).all()
    used_count = sum(1 for c in claims if c.status == ClaimStatus.USED)
    bl_name = names.get(k.blacklisted_by_device_uuid, (None, False))[0] if k.blacklisted_by_device_uuid else None

    return {
        "key": {
            "id": k.id,
            "code": k.code,
            "source": k.source.value,
            "created_at": k.created_at.isoformat(),
            "is_blacklisted": bool(k.is_blacklisted),
            "blacklisted_by_device_uuid": k.blacklisted_by_device_uuid,
            "blacklisted_by_name": bl_name,
            "blacklist_reason": k.blacklist_reason,
            "blacklisted_at": k.blacklisted_at.isoformat() if k.blacklisted_at else None,
            "used_count": used_count,
            "total_devices": total_devices,
        },
        "claims": [
            {
                "device_uuid": c.device_uuid,
                "device_name": names.get(c.device_uuid, (c.device_uuid[:8], False))[0],
                "status": c.status.value,
                "duration_ms": c.duration_ms,
                "error_note": c.error_note,
                "created_at": c.created_at.isoformat(),
                "completed_at": c.completed_at.isoformat() if c.completed_at else None,
                "is_blacklist_source": bool(k.is_blacklisted and k.blacklisted_by_device_uuid == c.device_uuid),
            }
            for c in claims
        ],
    }


@router.post("/manual", response_model=ActionResponse)
def add_key_manual(payload: KeyCreateManual, db: Session = Depends(get_db)):
    code = payload.code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="Key code cannot be empty.")

    existing = db.query(RedeemKey).filter(RedeemKey.code == code).first()
    if existing:
        st_label = "Blacklisted" if existing.is_blacklisted else "Aktif"
        raise HTTPException(status_code=409, detail=f"Key code '{code}' sudah ada di database (Status: {st_label}).")

    new_key = RedeemKey(code=code, source=KeySource.MANUAL, is_blacklisted=False)
    db.add(new_key)
    db.commit()
    db.refresh(new_key)
    return ActionResponse(success=True, message=f"Key '{code}' berhasil ditambahkan.", data={"id": new_key.id})


@router.post("/bulk", response_model=KeyBulkImportResult)
def add_keys_bulk(payload: KeyCreateBulk, db: Session = Depends(get_db)):
    raw_lines = payload.raw_text.splitlines()
    total_lines = len(raw_lines)

    seen_in_batch = set()
    cleaned_codes = []
    invalid_count = 0

    for line in raw_lines:
        code = line.strip()
        if not code:
            continue
        if len(code) > 128 or len(code) < 1:
            invalid_count += 1
            continue
        norm = code.lower()
        if norm in seen_in_batch:
            continue
        seen_in_batch.add(norm)
        cleaned_codes.append(code)

    if not cleaned_codes:
        return KeyBulkImportResult(
            total_lines=total_lines, saved_count=0,
            duplicates_or_skipped=total_lines, invalid_count=invalid_count,
        )

    CHUNK_SIZE = 500
    now = utc_now_naive()
    total_saved = 0

    for i in range(0, len(cleaned_codes), CHUNK_SIZE):
        chunk = cleaned_codes[i:i + CHUNK_SIZE]
        records = [
            {"code": c, "source": KeySource.BULK, "is_blacklisted": False, "created_at": now}
            for c in chunk
        ]
        stmt = mysql_insert(RedeemKey).values(records).prefix_with("IGNORE")
        res = db.execute(stmt)
        total_saved += res.rowcount

    db.commit()
    return KeyBulkImportResult(
        total_lines=total_lines,
        saved_count=total_saved,
        duplicates_or_skipped=total_lines - total_saved,
        invalid_count=invalid_count,
    )


@router.post("/{key_id}/reset", response_model=ActionResponse)
def reset_key_status(key_id: int, device_uuid: Optional[str] = Query(default=None), db: Session = Depends(get_db)):
    key = db.query(RedeemKey).filter(RedeemKey.id == key_id).first()
    if not key:
        raise HTTPException(status_code=404, detail="Key not found.")

    if device_uuid:
        claim = db.query(DeviceKeyClaim).filter(
            DeviceKeyClaim.key_id == key_id,
            DeviceKeyClaim.device_uuid == device_uuid,
        ).first()
        if not claim:
            raise HTTPException(status_code=404, detail="Klaim untuk device ini tidak ditemukan.")
        if claim.status == ClaimStatus.IN_PROGRESS:
            raise HTTPException(status_code=409, detail="Tidak bisa mereset klaim yang sedang 'in_progress'.")
        db.delete(claim)
        db.commit()
        return ActionResponse(success=True, message=f"Klaim key '{key.code}' pada device ini berhasil direset.")

    # Global blacklist reset: clear blacklist + delete all INVALID claims so devices can retry
    key.is_blacklisted = False
    key.blacklisted_by_device_uuid = None
    key.blacklist_reason = None
    key.blacklisted_at = None
    db.query(DeviceKeyClaim).filter(
        DeviceKeyClaim.key_id == key_id,
        DeviceKeyClaim.status == ClaimStatus.INVALID,
    ).delete()
    db.commit()
    return ActionResponse(success=True, message=f"Blacklist global untuk key '{key.code}' berhasil dicabut.")


@router.delete("/{key_id}", response_model=ActionResponse)
def delete_key(key_id: int, db: Session = Depends(get_db)):
    key = db.query(RedeemKey).filter(RedeemKey.id == key_id).first()
    if not key:
        raise HTTPException(status_code=404, detail="Key not found.")
    code = key.code
    db.delete(key)
    db.commit()
    return ActionResponse(success=True, message=f"Master key '{code}' berhasil dihapus.")


@router.delete("/actions/clear-invalid", response_model=ActionResponse)
def clear_all_invalid_keys(db: Session = Depends(get_db)):
    deleted_count = db.query(RedeemKey).filter(RedeemKey.is_blacklisted == True).delete()  # noqa: E712
    db.commit()
    return ActionResponse(success=True, message=f"Berhasil menghapus {deleted_count} key blacklisted.")


@router.get("/actions/export")
def export_unused_keys(device_uuid: Optional[str] = Query(default=None), db: Session = Depends(get_db)):
    if device_uuid:
        rows = db.execute(text(
            "SELECT rk.code FROM redeem_keys rk "
            "WHERE rk.is_blacklisted=0 AND NOT EXISTS ("
            "  SELECT 1 FROM device_key_claims c WHERE c.key_id=rk.id AND c.device_uuid=:d"
            ") ORDER BY rk.id ASC"
        ), {"d": device_uuid}).all()
    else:
        rows = db.execute(text(
            "SELECT rk.code FROM redeem_keys rk "
            "WHERE rk.is_blacklisted=0 AND NOT EXISTS ("
            "  SELECT 1 FROM device_key_claims c WHERE c.key_id=rk.id AND c.status='USED'"
            ") ORDER BY rk.id ASC"
        )).all()
    content = "\n".join(r[0] for r in rows)
    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=unused_keys.txt"},
    )
