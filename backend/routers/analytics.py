import datetime
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, case, text

from backend.config import settings
from backend.database import get_db
from backend.models import (
    Instance, RedeemKey, DeviceKeyClaim, ClaimStatus,
    QuizLog, QuizResultStatus, SystemLog,
)
from backend.instance_manager import instance_manager
from backend.time_service import time_service, normalize_tz, get_tz_meta

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])


ROLLING_AVG_WINDOW = 5


def _avg_ms_to_sec(val) -> float:
    return round(float(val) / 1000.0, 2) if val else 0.0


def _avg_list(values) -> float | None:
    return (sum(values) / len(values)) if values else None


def _recent_quiz_duration_avg(db: Session, device_uuid: str | None = None) -> float | None:
    q = db.query(QuizLog.duration_ms).filter(
        QuizLog.duration_ms.isnot(None),
        QuizLog.duration_ms > 0,
    )
    if device_uuid is not None:
        q = q.filter(QuizLog.device_uuid == device_uuid)
    vals = [r[0] for r in q.order_by(QuizLog.id.desc()).limit(ROLLING_AVG_WINDOW).all()]
    return _avg_list(vals)


def _recent_ai_duration_avg(db: Session, device_uuid: str | None = None) -> float | None:
    q = db.query(QuizLog.ai_duration_ms).filter(
        QuizLog.ai_duration_ms.isnot(None),
        QuizLog.ai_duration_ms > 0,
    )
    if device_uuid is not None:
        q = q.filter(QuizLog.device_uuid == device_uuid)
    vals = [r[0] for r in q.order_by(QuizLog.id.desc()).limit(ROLLING_AVG_WINDOW).all()]
    return _avg_list(vals)


def _recent_redeem_duration_avg(db: Session, device_uuid: str | None = None) -> float | None:
    ts_col = func.coalesce(DeviceKeyClaim.completed_at, DeviceKeyClaim.created_at)
    q = db.query(DeviceKeyClaim.duration_ms).filter(
        DeviceKeyClaim.status == ClaimStatus.USED,
        DeviceKeyClaim.duration_ms.isnot(None),
        DeviceKeyClaim.duration_ms > 0,
    )
    if device_uuid is not None:
        q = q.filter(DeviceKeyClaim.device_uuid == device_uuid)
    vals = [r[0] for r in q.order_by(ts_col.desc(), DeviceKeyClaim.id.desc()).limit(ROLLING_AVG_WINDOW).all()]
    return _avg_list(vals)


@router.get("/time")
def get_server_time():
    """Returns internet-synchronized UTC UNIX timestamp, sync status, and next KSA reset."""
    return time_service.status_payload()


@router.get("/kpi")
def get_kpi_summary(
    tz: str = Query(default="wib", description="Timezone: 'wib' (UTC+7) or 'ksa' (UTC+3)"),
    db: Session = Depends(get_db),
):
    tz_key = normalize_tz(tz)
    tz_meta = get_tz_meta(tz_key)
    snap = instance_manager.snapshot()

    # Global key inventory
    krow = db.execute(text(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN rk.is_blacklisted=1 THEN 1 ELSE 0 END) AS blacklisted, "
        "SUM(CASE WHEN rk.is_blacklisted=0 AND EXISTS ("
        "  SELECT 1 FROM device_key_claims c WHERE c.key_id=rk.id AND c.status='USED'"
        ") THEN 1 ELSE 0 END) AS claimed "
        "FROM redeem_keys rk"
    )).first()
    total_master = int(krow[0] or 0)
    blacklisted = int(krow[1] or 0)
    claimed_master = int(krow[2] or 0)
    unused_pure = max(0, total_master - blacklisted - claimed_master)
    used_claims = db.query(DeviceKeyClaim).filter(DeviceKeyClaim.status == ClaimStatus.USED).count()

    # Timezone-aware calendar boundaries based on internet-synced UTC clock
    today_start_utc = time_service.tz_day_start_utc_naive(tz_key, days_ago=0)
    d7_start_utc = time_service.tz_day_start_utc_naive(tz_key, days_ago=6)
    d30_start_utc = time_service.tz_day_start_utc_naive(tz_key, days_ago=29)

    # Global quiz stats (single aggregate query across all history incl. archived instances)
    q_global = db.query(
        func.count(QuizLog.id),
        func.sum(case((QuizLog.created_at >= today_start_utc, 1), else_=0)),
        func.sum(case((QuizLog.created_at >= d7_start_utc, 1), else_=0)),
        func.sum(case((QuizLog.created_at >= d30_start_utc, 1), else_=0)),
        func.sum(case((QuizLog.result_status == QuizResultStatus.CORRECT, 1), else_=0)),
    ).first()
    total_quiz = int(q_global[0] or 0)
    today_quiz = int(q_global[1] or 0)
    quiz_7d = int(q_global[2] or 0)
    quiz_30d = int(q_global[3] or 0)
    correct_count = int(q_global[4] or 0)
    incorrect_count = max(0, total_quiz - correct_count)
    accuracy = round((correct_count / total_quiz * 100), 1) if total_quiz > 0 else 0.0

    # Global durations: rolling average of the last 5 attempts (ignoring NULL/0 rows)
    avg_dur_ms = _recent_quiz_duration_avg(db)
    avg_ai_ms = _recent_ai_duration_avg(db)
    avg_redeem_ms = _recent_redeem_duration_avg(db)

    recovery_count = db.query(SystemLog).filter(SystemLog.category == "APP_RECOVERY").count()

    # Per-instance aggregates (total, today, 7d, 30d in one grouped query)
    quiz_counts_by_dev = {
        r[0]: (int(r[1] or 0), int(r[2] or 0), int(r[3] or 0), int(r[4] or 0))
        for r in db.query(
            QuizLog.device_uuid,
            func.count(QuizLog.id),
            func.sum(case((QuizLog.created_at >= today_start_utc, 1), else_=0)),
            func.sum(case((QuizLog.created_at >= d7_start_utc, 1), else_=0)),
            func.sum(case((QuizLog.created_at >= d30_start_utc, 1), else_=0)),
        )
        .filter(QuizLog.device_uuid.isnot(None))
        .group_by(QuizLog.device_uuid)
        .all()
    }
    rec_by_dev = {
        r[0]: int(r[1] or 0)
        for r in db.query(SystemLog.device_uuid, func.count(SystemLog.id))
        .filter(SystemLog.category == "APP_RECOVERY", SystemLog.device_uuid.isnot(None))
        .group_by(SystemLog.device_uuid).all()
    }

    inst_rows = []
    online_count = 0
    running_count = 0
    idle_count = 0
    offline_count = 0
    total_app_keys = 0

    for item in snap:
        uid = item["uuid"]
        st = item["status"]
        if st != "OFFLINE" and st != "ERROR":
            online_count += 1
        if st == "RUNNING":
            running_count += 1
        elif st == "IDLE":
            idle_count += 1
        elif st == "OFFLINE":
            offline_count += 1
        if st != "OFFLINE" and isinstance(item.get("current_app_keys"), int):
            total_app_keys += item["current_app_keys"]

        q_total, q_today, q_7d, q_30d = quiz_counts_by_dev.get(uid, (0, 0, 0, 0))
        q_dur = _recent_quiz_duration_avg(db, device_uuid=uid)
        q_ai = _recent_ai_duration_avg(db, device_uuid=uid)
        r_dur = _recent_redeem_duration_avg(db, device_uuid=uid)
        rt = instance_manager.rt(uid)
        inst_rows.append({
            "uuid": uid,
            "name": item["name"],
            "address": item["address"],
            "status": st,
            "current_app_keys": item.get("current_app_keys"),
            "eligible_keys": item["eligible_keys"],
            "used_keys": item["used_keys"],
            "invalid_keys": item["invalid_keys"],
            "quiz_total": q_total,
            "quiz_today": q_today,
            "quiz_7d": q_7d,
            "quiz_30d": q_30d,
            "avg_duration_sec": _avg_ms_to_sec(q_dur),
            "avg_ai_duration_sec": round(float(q_ai) / 1000.0, 2) if q_ai is not None else None,
            "avg_redeem_duration_sec": _avg_ms_to_sec(r_dur),
            "recovery_count": rec_by_dev.get(uid, 0),
            "circuit_breaker_limit": settings.circuit_breaker_limit,
            "consecutive_recoveries": rt.consecutive_recoveries,
        })

    return {
        "timezone": tz_key,
        "timezone_label": tz_meta["label"],
        "server_time": time_service.status_payload(),
        "global": {
            "total_instances": len(snap),
            "online_instances": online_count,
            "total_app_keys": total_app_keys,
            "unused_pure": unused_pure,
            "blacklisted": blacklisted,
            "total_master_keys": total_master,
            "used_claims": used_claims,
            "total_quiz_answered": total_quiz,
            "today_quiz_answered": today_quiz,
            "quiz_7d_answered": quiz_7d,
            "quiz_30d_answered": quiz_30d,
            "accuracy_percentage": accuracy,
            "correct": correct_count,
            "incorrect": incorrect_count,
            "avg_duration_sec": _avg_ms_to_sec(avg_dur_ms),
            "avg_ai_duration_sec": round(float(avg_ai_ms) / 1000.0, 2) if avg_ai_ms is not None else None,
            "avg_redeem_duration_sec": _avg_ms_to_sec(avg_redeem_ms),
            "auto_recovery_count": recovery_count,
            "running_count": running_count,
            "idle_count": idle_count,
            "offline_count": offline_count,
        },
        "instances": inst_rows,
    }


@router.get("/charts")
def get_chart_data(
    timeframe: str = Query(default="24h"),
    device_uuid: str = Query(default="all"),
    tz: str = Query(default="wib", description="Timezone: 'wib' (UTC+7) or 'ksa' (UTC+3)"),
    db: Session = Depends(get_db),
):
    tz_key = normalize_tz(tz)
    tz_meta = get_tz_meta(tz_key)
    offset_h = int(tz_meta["offset_hours"])  # strictly validated int: 7 or 3

    now = time_service.utc_now_naive()
    today_start_utc = time_service.tz_day_start_utc_naive(tz_key, days_ago=0)
    d7_start_utc = time_service.tz_day_start_utc_naive(tz_key, days_ago=6)
    d30_start_utc = time_service.tz_day_start_utc_naive(tz_key, days_ago=29)

    if timeframe == "today":
        start_time = today_start_utc
        group_fmt = "%H:00"
    elif timeframe == "24h":
        start_time = now - datetime.timedelta(hours=24)
        group_fmt = "%H:00"
    elif timeframe == "7d":
        start_time = d7_start_utc
        group_fmt = "%Y-%m-%d"
    elif timeframe == "30d":
        start_time = d30_start_utc
        group_fmt = "%Y-%m-%d"
    else:
        start_time = datetime.datetime(2020, 1, 1)
        group_fmt = "%Y-%m-%d"

    specific = device_uuid if (device_uuid and device_uuid != "all") else None
    interval_expr = text(f"INTERVAL {offset_h} HOUR")

    # 0. Quiz volume summary (today / 7d / 30d / all) for the selected scope & timezone
    vol_q = db.query(
        func.sum(case((QuizLog.created_at >= today_start_utc, 1), else_=0)),
        func.sum(case((QuizLog.created_at >= d7_start_utc, 1), else_=0)),
        func.sum(case((QuizLog.created_at >= d30_start_utc, 1), else_=0)),
        func.count(QuizLog.id),
    )
    if specific:
        vol_q = vol_q.filter(QuizLog.device_uuid == specific)
    vol_row = vol_q.first()
    quiz_volume = {
        "today": int(vol_row[0] or 0),
        "d7": int(vol_row[1] or 0),
        "d30": int(vol_row[2] or 0),
        "all": int(vol_row[3] or 0),
    }

    # 1. Key distribution
    if specific:
        row = db.execute(text(
            "SELECT "
            "SUM(CASE WHEN rk.is_blacklisted=0 AND c.id IS NULL THEN 1 ELSE 0 END) AS avail, "
            "SUM(CASE WHEN c.status='USED' THEN 1 ELSE 0 END) AS used_c, "
            "SUM(CASE WHEN c.status='INVALID' OR (rk.is_blacklisted=1 AND (c.status IS NULL OR c.status<>'USED')) THEN 1 ELSE 0 END) AS inv_c "
            "FROM redeem_keys rk LEFT JOIN device_key_claims c ON c.key_id=rk.id AND c.device_uuid=:d"
        ), {"d": specific}).first()
        key_distribution = [
            {"status": "available", "count": int(row[0] or 0)},
            {"status": "used", "count": int(row[1] or 0)},
            {"status": "invalid", "count": int(row[2] or 0)},
        ]
    else:
        row = db.execute(text(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN rk.is_blacklisted=1 THEN 1 ELSE 0 END) AS blacklisted, "
            "SUM(CASE WHEN rk.is_blacklisted=0 AND EXISTS ("
            "  SELECT 1 FROM device_key_claims c WHERE c.key_id=rk.id AND c.status='USED'"
            ") THEN 1 ELSE 0 END) AS used_partial "
            "FROM redeem_keys rk"
        )).first()
        total_m = int(row[0] or 0)
        bl = int(row[1] or 0)
        up = int(row[2] or 0)
        key_distribution = [
            {"status": "unused_pure", "count": max(0, total_m - bl - up)},
            {"status": "used_partial", "count": up},
            {"status": "blacklisted", "count": bl},
        ]

    # 2. Door category performance
    door_q = db.query(
        QuizLog.door_category,
        func.count(QuizLog.id).label("total"),
        func.sum(case((QuizLog.result_status == QuizResultStatus.CORRECT, 1), else_=0)).label("correct"),
    )
    if specific:
        door_q = door_q.filter(QuizLog.device_uuid == specific)
    door_performance = [
        {
            "door": d[0],
            "total": d[1],
            "correct": int(d[2] or 0),
            "accuracy": round((int(d[2] or 0) / d[1] * 100), 1) if d[1] > 0 else 0.0,
        }
        for d in door_q.group_by(QuizLog.door_category).all()
    ]

    # 3. Quiz activity trend (timezone-shifted buckets)
    shifted_quiz_ts = func.date_add(QuizLog.created_at, interval_expr)
    qt_q = db.query(
        func.date_format(shifted_quiz_ts, group_fmt).label("bucket"),
        func.count(QuizLog.id).label("total"),
        func.sum(case((QuizLog.result_status == QuizResultStatus.CORRECT, 1), else_=0)).label("correct"),
        func.avg(case((QuizLog.duration_ms > 0, QuizLog.duration_ms), else_=None)).label("avg_duration"),
        func.avg(case((QuizLog.ai_duration_ms > 0, QuizLog.ai_duration_ms), else_=None)).label("avg_ai"),
    ).filter(QuizLog.created_at >= start_time)
    if specific:
        qt_q = qt_q.filter(QuizLog.device_uuid == specific)
    quiz_trend = [
        {
            "time": r[0],
            "total": r[1],
            "correct": int(r[2] or 0),
            "avg_duration_sec": _avg_ms_to_sec(r[3]),
            "avg_ai_duration_sec": round(float(r[4]) / 1000.0, 2) if r[4] is not None else None,
        }
        for r in qt_q.group_by("bucket").order_by(func.min(QuizLog.created_at)).all()
    ]

    # 4. Redeem activity trend (timezone-shifted buckets)
    ts_col = func.coalesce(DeviceKeyClaim.completed_at, DeviceKeyClaim.created_at)
    shifted_redeem_ts = func.date_add(ts_col, interval_expr)
    rt_q = db.query(
        func.date_format(shifted_redeem_ts, group_fmt).label("bucket"),
        func.sum(case((DeviceKeyClaim.status == ClaimStatus.USED, 1), else_=0)).label("used"),
        func.sum(case((DeviceKeyClaim.status == ClaimStatus.INVALID, 1), else_=0)).label("invalid"),
    ).filter(
        DeviceKeyClaim.status.in_([ClaimStatus.USED, ClaimStatus.INVALID]),
        ts_col >= start_time,
    )
    if specific:
        rt_q = rt_q.filter(DeviceKeyClaim.device_uuid == specific)
    redeem_trend = [
        {"time": r[0], "used": int(r[1] or 0), "invalid": int(r[2] or 0)}
        for r in rt_q.group_by("bucket").order_by(func.min(ts_col)).all()
    ]

    # 5. Instance comparison (for global view)
    instance_comparison = []
    if not specific:
        active_insts = db.query(Instance).filter(Instance.is_deleted == False).order_by(Instance.created_at.asc()).all()  # noqa: E712
        q_stats = {
            r[0]: (int(r[1] or 0), r[2], r[3])
            for r in db.query(
                QuizLog.device_uuid,
                func.count(QuizLog.id),
                func.avg(case((QuizLog.duration_ms > 0, QuizLog.duration_ms), else_=None)),
                func.avg(case((QuizLog.ai_duration_ms > 0, QuizLog.ai_duration_ms), else_=None)),
            ).filter(QuizLog.created_at >= start_time, QuizLog.device_uuid.isnot(None)).group_by(QuizLog.device_uuid).all()
        }
        c_stats = {
            r[0]: int(r[1] or 0)
            for r in db.query(DeviceKeyClaim.device_uuid, func.count(DeviceKeyClaim.id))
            .filter(
                DeviceKeyClaim.status == ClaimStatus.USED,
                func.coalesce(DeviceKeyClaim.completed_at, DeviceKeyClaim.created_at) >= start_time,
            ).group_by(DeviceKeyClaim.device_uuid).all()
        }
        for inst in active_insts:
            qt, qd, qa = q_stats.get(inst.uuid, (0, None, None))
            instance_comparison.append({
                "uuid": inst.uuid,
                "name": inst.name,
                "quiz_total": qt,
                "keys_used": c_stats.get(inst.uuid, 0),
                "avg_duration_sec": _avg_ms_to_sec(qd),
                "avg_ai_duration_sec": round(float(qa) / 1000.0, 2) if qa is not None else None,
            })

    return {
        "timeframe": timeframe,
        "device_uuid": device_uuid,
        "timezone": tz_key,
        "timezone_label": tz_meta["label"],
        "quiz_volume": quiz_volume,
        "key_distribution": key_distribution,
        "door_performance": door_performance,
        "quiz_trend": quiz_trend,
        "redeem_trend": redeem_trend,
        "instance_comparison": instance_comparison,
    }
