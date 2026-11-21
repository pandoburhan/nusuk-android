"""
v1 -> v2 database migration (Zero-Data-Loss, idempotent). See blueprint-update/6.md.

Guarantees:
  * Never DROPs / TRUNCATEs / DELETEs any production row or column.
  * Old `redeem_keys` is snapshotted once into `redeem_keys_v1_archive`.
  * Data-mapping steps run only until the marker `v2_migration_done` is stored in
    bot_settings (set after verification passes). Re-running afterwards only verifies,
    so v2 operator actions (blacklist reset, claim reset) are never overwritten.

Run manually:  python -m backend.migrations.v2_migration
(It is also invoked automatically at backend startup.)
"""
import datetime
import logging
from typing import Dict, Tuple

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from backend.config import settings
from backend.database import engine, Base

logger = logging.getLogger("v2_migration")

MARKER = "v2_migration_done"
LEGACY_ALIAS = "Primary Instance (Legacy)"


def _cols(eng: Engine, table: str) -> set:
    return {c["name"] for c in inspect(eng).get_columns(table)}


def _has_table(eng: Engine, table: str) -> bool:
    return inspect(eng).has_table(table)


def _parse_target(target: str) -> Tuple[str, int]:
    target = (target or "").strip()
    if ":" in target:
        ip, port = target.rsplit(":", 1)
        try:
            return ip, int(port)
        except ValueError:
            return ip, 5555
    return target or "127.0.0.1", 5555


def _legacy_counts(conn) -> Dict[str, int]:
    """Counts derived from the preserved v1 columns (valid before and after migration)."""
    q = lambda s: conn.execute(text(s)).scalar() or 0
    return {
        "keys_total": q("SELECT COUNT(*) FROM redeem_keys"),
        "keys_used": q("SELECT COUNT(*) FROM redeem_keys WHERE status='USED'"),
        "keys_invalid": q("SELECT COUNT(*) FROM redeem_keys WHERE status='INVALID'"),
        "quiz_total": q("SELECT COUNT(*) FROM quiz_logs"),
        "app_recovery": q("SELECT COUNT(*) FROM system_logs WHERE category='APP_RECOVERY'"),
        "syslog_non_migration": q("SELECT COUNT(*) FROM system_logs WHERE category<>'SYSTEM_MIGRATION'"),
        "bot_settings": q("SELECT COUNT(*) FROM bot_settings"),
    }


def run_migration(eng: Engine = engine) -> Dict[str, object]:
    from backend import models  # noqa: F401  (register tables)

    report: Dict[str, object] = {"ok": False, "steps": []}
    steps = report["steps"]

    # ---- Step 0: baseline (before any change) ----
    with eng.connect() as c:
        before = _legacy_counts(c)
    report["before"] = before

    # ---- Step 1: one-time archive snapshot of v1 redeem_keys ----
    if not _has_table(eng, "redeem_keys_v1_archive"):
        with eng.begin() as c:
            c.execute(text("CREATE TABLE redeem_keys_v1_archive AS SELECT * FROM redeem_keys"))
        steps.append("archive redeem_keys_v1_archive created")

    # ---- Step 2: new tables ----
    Base.metadata.create_all(
        bind=eng,
        tables=[models.Instance.__table__, models.DeviceKeyClaim.__table__],
    )

    # ---- Step 3: additive columns (nullable / defaulted first) ----
    alters = [
        ("redeem_keys", "is_blacklisted", "TINYINT(1) NOT NULL DEFAULT 0"),
        ("redeem_keys", "blacklisted_by_device_uuid", "VARCHAR(36) NULL"),
        ("redeem_keys", "blacklist_reason", "TEXT NULL"),
        ("redeem_keys", "blacklisted_at", "DATETIME NULL"),
        ("quiz_logs", "device_uuid", "VARCHAR(36) NULL"),
        ("quiz_logs", "ai_duration_ms", "INT NULL DEFAULT NULL"),
        ("system_logs", "device_uuid", "VARCHAR(36) NULL"),
        ("instances", "connection_type", "VARCHAR(16) NOT NULL DEFAULT 'tcp'"),
        ("instances", "adb_serial", "VARCHAR(128) NULL"),
    ]
    for table, col, ddl in alters:
        if col not in _cols(eng, table):
            with eng.begin() as c:
                c.execute(text(f"ALTER TABLE `{table}` ADD COLUMN `{col}` {ddl}"))
            steps.append(f"added {table}.{col}")

    # Allow NULL ip/port for physical USB instances (idempotent) & backfill adb_serial on existing rows
    try:
        inst_cols = {c["name"]: c for c in inspect(eng).get_columns("instances")}
        if inst_cols.get("ip") and not inst_cols["ip"].get("nullable", True):
            with eng.begin() as c:
                c.execute(text("ALTER TABLE `instances` MODIFY COLUMN `ip` VARCHAR(64) NULL"))
            steps.append("instances.ip made nullable")
        if inst_cols.get("port") and not inst_cols["port"].get("nullable", True):
            with eng.begin() as c:
                c.execute(text("ALTER TABLE `instances` MODIFY COLUMN `port` INT NULL DEFAULT 5555"))
            steps.append("instances.port made nullable")
        with eng.begin() as c:
            res = c.execute(text(
                "UPDATE instances SET adb_serial = CONCAT(ip, ':', port) "
                "WHERE (adb_serial IS NULL OR adb_serial = '') AND ip IS NOT NULL AND port IS NOT NULL"
            ))
            if res.rowcount:
                steps.append(f"backfilled instances.adb_serial ({res.rowcount} rows)")
            c.execute(text(
                "UPDATE instances SET connection_type = 'tcp' "
                "WHERE connection_type IS NULL OR connection_type = ''"
            ))
    except Exception as e:
        logger.warning("Instance USB/Wireless schema check warning: %s", e)

    # indexes (ignore if exist)
    for table, col in [("quiz_logs", "device_uuid"), ("system_logs", "device_uuid"),
                       ("redeem_keys", "is_blacklisted"), ("instances", "adb_serial")]:
        idx = f"ix_{table}_{col}"
        existing = {i["name"] for i in inspect(eng).get_indexes(table)}
        if idx not in existing:
            with eng.begin() as c:
                c.execute(text(f"CREATE INDEX `{idx}` ON `{table}` (`{col}`)"))
            steps.append(f"index {idx}")

    # ---- Step 4: has data mapping already been completed? ----
    with eng.connect() as c:
        done = c.execute(text("SELECT setting_value FROM bot_settings WHERE setting_key=:k"),
                         {"k": MARKER}).scalar()
    legacy_uuid = None
    with eng.connect() as c:
        legacy_uuid = c.execute(text("SELECT uuid FROM instances WHERE is_legacy_primary=1 LIMIT 1")).scalar()

    if not done:
        # ---- Step 5: Legacy Primary Instance (created once) ----
        if not legacy_uuid:
            with eng.connect() as c:
                tgt = c.execute(text("SELECT setting_value FROM bot_settings WHERE setting_key='adb_target'")).scalar()
            ip, port = _parse_target(tgt or settings.adb_target)
            legacy_uuid = models.new_uuid()
            now = datetime.datetime.utcnow()
            with eng.begin() as c:
                c.execute(text(
                    "INSERT INTO instances (uuid, name, ip, port, live_preview_enabled, nusuk_installed, "
                    "anchor_planted, is_legacy_primary, is_deleted, created_at, last_status) "
                    "VALUES (:u, :n, :ip, :p, 1, NULL, 0, 1, 0, :now, 'OFFLINE')"),
                    {"u": legacy_uuid, "n": LEGACY_ALIAS, "ip": ip, "p": port, "now": now})
            steps.append(f"legacy instance {legacy_uuid} -> {ip}:{port}")
        report["legacy_uuid"] = legacy_uuid

        with eng.begin() as c:
            # ---- Step 6: ownership of historical rows (ai_duration_ms stays NULL) ----
            c.execute(text("UPDATE quiz_logs SET device_uuid=:u WHERE device_uuid IS NULL"), {"u": legacy_uuid})
            c.execute(text("UPDATE system_logs SET device_uuid=:u WHERE device_uuid IS NULL "
                           "AND category<>'SYSTEM_MIGRATION'"), {"u": legacy_uuid})

            # ---- Step 7: claims. used -> USED claim; invalid -> INVALID claim + global blacklist ----
            c.execute(text(
                "INSERT IGNORE INTO device_key_claims "
                "(key_id, device_uuid, status, duration_ms, error_note, created_at, completed_at) "
                "SELECT id, :u, 'USED', duration_ms, error_note, COALESCE(used_at, created_at), used_at "
                "FROM redeem_keys WHERE status='USED'"), {"u": legacy_uuid})
            c.execute(text(
                "INSERT IGNORE INTO device_key_claims "
                "(key_id, device_uuid, status, duration_ms, error_note, created_at, completed_at) "
                "SELECT id, :u, 'INVALID', duration_ms, error_note, COALESCE(used_at, created_at), used_at "
                "FROM redeem_keys WHERE status='INVALID'"), {"u": legacy_uuid})
            c.execute(text(
                "UPDATE redeem_keys SET is_blacklisted=1, blacklisted_by_device_uuid=:u, "
                "blacklist_reason=error_note, blacklisted_at=COALESCE(used_at, created_at) "
                "WHERE status='INVALID' AND is_blacklisted=0"), {"u": legacy_uuid})
            # unused / in_progress / used rows: is_blacklisted stays 0 (column default), no claim for
            # unused & in_progress (safe rollback semantics).
    else:
        report["legacy_uuid"] = legacy_uuid

    # ---- Step 8: verification (always) ----
    problems = []
    with eng.connect() as c:
        after = _legacy_counts(c)
        luid = legacy_uuid
        used_claims = c.execute(text(
            "SELECT COUNT(*) FROM device_key_claims WHERE device_uuid=:u AND status='USED' "
            "AND key_id IN (SELECT id FROM redeem_keys WHERE status='USED')"), {"u": luid}).scalar() or 0
        inv_claims = c.execute(text(
            "SELECT COUNT(*) FROM device_key_claims WHERE device_uuid=:u AND status='INVALID' "
            "AND key_id IN (SELECT id FROM redeem_keys WHERE status='INVALID')"), {"u": luid}).scalar() or 0
        legacy_recovery = c.execute(text(
            "SELECT COUNT(*) FROM system_logs WHERE category='APP_RECOVERY' AND device_uuid=:u"),
            {"u": luid}).scalar() or 0
        legacy_quiz = c.execute(text("SELECT COUNT(*) FROM quiz_logs WHERE device_uuid=:u"), {"u": luid}).scalar() or 0
        ai_non_null_legacy = c.execute(text(
            "SELECT COUNT(*) FROM quiz_logs WHERE device_uuid=:u AND ai_duration_ms IS NOT NULL "
            "AND created_at < (SELECT created_at FROM instances WHERE uuid=:u)"), {"u": luid}).scalar() or 0
        archive_total = c.execute(text("SELECT COUNT(*) FROM redeem_keys_v1_archive")).scalar() or 0
    report["after"] = after

    if after["quiz_total"] != before["quiz_total"] and not done:
        problems.append("quiz_logs count changed")
    if after["keys_total"] != before["keys_total"] and not done:
        problems.append("redeem_keys count changed")
    if not done:
        if used_claims != after["keys_used"]:
            problems.append(f"used claims {used_claims} != legacy used {after['keys_used']}")
        if inv_claims != after["keys_invalid"]:
            problems.append(f"invalid claims {inv_claims} != legacy invalid {after['keys_invalid']}")
        if legacy_recovery != after["app_recovery"]:
            problems.append(f"APP_RECOVERY legacy {legacy_recovery} != {after['app_recovery']}")
        if legacy_quiz != after["quiz_total"]:
            problems.append(f"quiz_logs owned {legacy_quiz} != {after['quiz_total']}")
        if ai_non_null_legacy:
            problems.append("legacy quiz rows have non-NULL ai_duration_ms")
        if archive_total != before["keys_total"]:
            problems.append(f"archive rows {archive_total} != {before['keys_total']}")
    report["problems"] = problems
    report["ok"] = not problems

    # ---- Step 9: marker + audit log (only the first successful run) ----
    if report["ok"] and not done:
        msg = (f"v1->v2 migration OK. keys={before['keys_total']}->{after['keys_total']}, "
               f"used+invalid claims={used_claims + inv_claims}/{before['keys_used'] + before['keys_invalid']}, "
               f"quiz={before['quiz_total']}->{after['quiz_total']}, "
               f"APP_RECOVERY={legacy_recovery}/{before['app_recovery']}, legacy_uuid={legacy_uuid}")
        with eng.begin() as c:
            c.execute(text(
                "INSERT INTO bot_settings (setting_key, setting_value, updated_at) VALUES (:k, :v, :t)"),
                {"k": MARKER, "v": datetime.datetime.utcnow().isoformat(), "t": datetime.datetime.utcnow()})
            c.execute(text(
                "INSERT INTO system_logs (severity, category, message, device_uuid, created_at) "
                "VALUES ('INFO', 'SYSTEM_MIGRATION', :m, NULL, :t)"),
                {"m": msg, "t": datetime.datetime.utcnow()})
        steps.append("marker stored")
    elif problems:
        logger.error("Migration verification FAILED: %s", problems)

    return report


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(run_migration(), indent=2, default=str))
