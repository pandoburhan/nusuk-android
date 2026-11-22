"""
Instance Manager + Background Connection Watchdog (blueprint 1.3 / 1.4 / 3.5).
Holds per-device runtime state (thread-safe), device registry CRUD (soft-delete, auto-reconciliation
via on-device UUID anchor) and the watchdog that silently reconnects OFFLINE devices.
"""
import datetime
import ipaddress
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Any, Tuple

from sqlalchemy import text

from backend.config import settings
from backend.database import SessionLocal
from backend.models import Instance, LogSeverity, BotSetting, new_uuid
from backend.adb_manager import adb_manager
from backend.event_log import log_event, set_device_name
from backend.telemetry import telemetry_manager
from backend.time_service import utc_now_naive

logger = logging.getLogger(__name__)

ONLINE_STATES = ("IDLE", "RUNNING", "RECOVERING", "STOPPING", "ERROR")
VALID_CONN_TYPES = ("tcp", "usb", "wireless")
_SERIAL_SAFE_RE = re.compile(r"^[A-Za-z0-9._:@/-]{2,120}$")


class TaskIntent:
    """Persistent desired task intent for an instance."""
    def __init__(
        self,
        mode: str,
        stop_mode: str = "limit",
        stop_limit: int = 100,
        completed_count: int = 0,
    ):
        self.mode = mode
        self.stop_mode = stop_mode or "limit"
        self.stop_limit = max(1, int(stop_limit or 100))
        self.completed_count = max(0, int(completed_count or 0))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "stop_mode": self.stop_mode,
            "stop_limit": self.stop_limit,
            "completed_count": self.completed_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskIntent":
        return cls(
            mode=data.get("mode", "auto"),
            stop_mode=data.get("stop_mode", "limit"),
            stop_limit=data.get("stop_limit", 100),
            completed_count=data.get("completed_count", 0),
        )


class LoginStatus:
    LOGGED_IN = "LOGGED_IN"
    LOGGED_OUT = "LOGGED_OUT"
    AWAITING_CREDENTIALS = "AWAITING_CREDENTIALS"
    AWAITING_OTP = "AWAITING_OTP"
    PROCESSING_LOGIN = "PROCESSING_LOGIN"


class Runtime:
    def __init__(self, last_app_keys: Optional[int] = None):
        self.status = "OFFLINE"
        self.mode: Optional[str] = None
        self.current_screen = "SCREEN_UNKNOWN_OR_LOADING"
        self.current_step = "Menunggu koneksi ADB..."
        self.current_app_keys: Optional[int] = last_app_keys
        self.start_time: Optional[float] = None
        self.consecutive_recoveries = 0
        self.offline_logged = False
        self.fail_count = 0
        self.next_probe_at = 0.0
        self.reboot_started: Optional[float] = None
        self.reboot_saw_down = False
        self.identity_warned = False
        self.last_seen_at: Optional[datetime.datetime] = None
        self.missed_heartbeats = 0
        self.stop_mode: str = "limit"
        self.stop_limit: int = 100
        self.session_completed_count: int = 0
        self.task_intent: Optional[TaskIntent] = None
        # Task queue — FIFO list of queued future tasks (persisted to BotSetting)
        self.task_queue: List[Dict] = []
        # Auto-Login state & thread-safe operator handoff (in-memory only, never persisted)
        self.login_status: str = LoginStatus.LOGGED_IN
        self.login_error: Optional[str] = None
        self.credentials_event = threading.Event()
        self.pending_credentials: Optional[Dict[str, str]] = None
        self.otp_event = threading.Event()
        self.pending_otp: Optional[str] = None


class InstanceManager:
    def __init__(self):
        self._lock = threading.RLock()
        self.runtime: Dict[str, Runtime] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._probe_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="probe")

    # ------------------------------------------------------------------ runtime state
    def rt(self, device_uuid: str) -> Runtime:
        with self._lock:
            r = self.runtime.get(device_uuid)
            if r is None:
                r = Runtime()
                self.runtime[device_uuid] = r
            return r

    def set_runtime(self, device_uuid: str, emit: bool = True, **kw):
        with self._lock:
            r = self.rt(device_uuid)
            for k, v in kw.items():
                setattr(r, k, v)
        if emit:
            self.emit_status(device_uuid)

    def status_payload(self, device_uuid: str) -> Dict[str, Any]:
        r = self.rt(device_uuid)
        uptime = int(time.time() - r.start_time) if r.start_time and r.status in ("RUNNING", "RECOVERING", "STOPPING") else 0
        ti = getattr(r, "task_intent", None)
        tq = getattr(r, "task_queue", [])
        next_q = tq[0] if tq else None
        return {
            "device_uuid": device_uuid, "status": r.status, "mode": r.mode,
            "current_screen": r.current_screen, "current_step": r.current_step,
            "uptime_seconds": uptime, "current_app_keys": r.current_app_keys,
            "stop_mode": getattr(r, "stop_mode", "limit"),
            "stop_limit": getattr(r, "stop_limit", 100),
            "session_completed_count": getattr(r, "session_completed_count", 0),
            "login_status": getattr(r, "login_status", LoginStatus.LOGGED_IN),
            "login_error": getattr(r, "login_error", None),
            "has_pending_task": bool(ti),
            "pending_task_mode": ti.mode if ti else None,
            "pending_task_stop_mode": ti.stop_mode if ti else None,
            "pending_task_stop_limit": ti.stop_limit if ti else None,
            "pending_task_completed": ti.completed_count if ti else 0,
            # Queue fields
            "task_queue": list(tq),
            "queue_length": len(tq),
            "has_queued_tasks": len(tq) > 0,
            "next_queued_mode": next_q["mode"] if next_q else None,
        }

    def emit_status(self, device_uuid: str):
        telemetry_manager.emit_sync("status_update", self.status_payload(device_uuid))

    # ------------------------------------------------------------------ auto-login handoff
    def submit_login_credentials(self, device_uuid: str, email: str, password: str) -> None:
        """Thread-safe injection of email/password from FastAPI into the waiting InstanceWorker."""
        with self._lock:
            r = self.rt(device_uuid)
            r.pending_credentials = {"email": email, "password": password}
            r.login_error = None
            r.login_status = LoginStatus.PROCESSING_LOGIN
            r.current_step = "Kredensial diterima — memproses Auto-Login..."
            r.credentials_event.set()
        self.emit_status(device_uuid)

    def consume_login_credentials(self, device_uuid: str) -> Optional[Dict[str, str]]:
        """Atomically pop and clear pending credentials from memory."""
        with self._lock:
            r = self.rt(device_uuid)
            creds = r.pending_credentials
            r.pending_credentials = None
            r.credentials_event.clear()
            return creds

    def submit_login_otp(self, device_uuid: str, otp_code: str) -> None:
        """Thread-safe injection of 4-digit OTP from FastAPI into the waiting InstanceWorker."""
        with self._lock:
            r = self.rt(device_uuid)
            r.pending_otp = otp_code
            r.login_error = None
            r.login_status = LoginStatus.PROCESSING_LOGIN
            r.current_step = "Kode OTP diterima — memverifikasi akun..."
            r.otp_event.set()
        self.emit_status(device_uuid)

    def consume_login_otp(self, device_uuid: str) -> Optional[str]:
        """Atomically pop and clear pending OTP from memory."""
        with self._lock:
            r = self.rt(device_uuid)
            otp = r.pending_otp
            r.pending_otp = None
            r.otp_event.clear()
            return otp

    def clear_login_inputs(self, device_uuid: str) -> None:
        """Scrub any unconsumed credentials/OTP from memory."""
        with self._lock:
            r = self.rt(device_uuid)
            r.pending_credentials = None
            r.credentials_event.clear()
            r.pending_otp = None
            r.otp_event.clear()

    def mark_offline(self, device_uuid: str, reason: str = "terputus"):
        """RUNNING/IDLE -> OFFLINE with exactly ONE WARNING log until it comes back."""
        self.clear_login_inputs(device_uuid)
        r = self.rt(device_uuid)
        first = not r.offline_logged
        ti = getattr(r, "task_intent", None)
        step_msg = (
            f"Perangkat offline — tugas '{ti.mode}' ({ti.completed_count}/{ti.stop_limit}) akan otomatis dilanjutkan saat terhubung..."
            if ti
            else "Perangkat offline - mencoba menyambung ulang di latar belakang..."
        )
        self.set_runtime(device_uuid, status="OFFLINE", mode=None, start_time=None,
                         current_step=step_msg,
                         offline_logged=True, next_probe_at=0.0, fail_count=0)
        if first:
            log_event(LogSeverity.WARNING, "CONNECTION",
                      f"Instance [{self.name_of(device_uuid)}] {reason} - status diubah ke OFFLINE. "
                      f"Mencoba menyambung ulang di latar belakang...", device_uuid)

    # ------------------------------------------------------------------ persistent task intent
    def set_task_intent(
        self,
        device_uuid: str,
        mode: str,
        stop_mode: str = "limit",
        stop_limit: int = 100,
        completed_count: int = 0,
    ) -> TaskIntent:
        intent = TaskIntent(mode=mode, stop_mode=stop_mode, stop_limit=stop_limit, completed_count=completed_count)
        with self._lock:
            r = self.rt(device_uuid)
            r.task_intent = intent
            r.stop_mode = intent.stop_mode
            r.stop_limit = intent.stop_limit
            r.session_completed_count = intent.completed_count
        self._persist_task_intent(device_uuid, intent)
        self.emit_status(device_uuid)
        return intent

    def get_task_intent(self, device_uuid: str) -> Optional[TaskIntent]:
        with self._lock:
            return getattr(self.rt(device_uuid), "task_intent", None)

    def update_task_progress(self, device_uuid: str, completed_count: int):
        intent = None
        with self._lock:
            r = self.rt(device_uuid)
            if r.task_intent:
                r.task_intent.completed_count = completed_count
                intent = r.task_intent
            r.session_completed_count = completed_count
        if intent:
            self._persist_task_intent(device_uuid, intent)
        self.emit_status(device_uuid)

    def clear_task_intent(self, device_uuid: str, reason: str = ""):
        with self._lock:
            r = self.rt(device_uuid)
            r.task_intent = None
        self._delete_task_intent(device_uuid)
        self.emit_status(device_uuid)

    # ------------------------------------------------------------------ task queue (FIFO per-device)
    def enqueue_task(
        self,
        device_uuid: str,
        mode: str,
        stop_mode: str = "limit",
        stop_limit: int = 100,
    ) -> Dict:
        """Add one task to the FIFO queue for this device. Returns the queue item dict."""
        import uuid as _uuid
        item = {
            "queue_id": str(_uuid.uuid4()),
            "mode": mode,
            "stop_mode": stop_mode or "limit",
            "stop_limit": max(1, int(stop_limit or 100)),
            "enqueued_at": utc_now_naive().isoformat(),
        }
        with self._lock:
            r = self.rt(device_uuid)
            r.task_queue.append(item)
            queue_copy = list(r.task_queue)
        self._persist_task_queue(device_uuid, queue_copy)
        self.emit_status(device_uuid)
        return item

    def dequeue_next_task(self, device_uuid: str) -> Optional[Dict]:
        """Pop and return the first item from the queue (FIFO). Returns None if empty."""
        with self._lock:
            r = self.rt(device_uuid)
            if not r.task_queue:
                return None
            item = r.task_queue.pop(0)
            queue_copy = list(r.task_queue)
        self._persist_task_queue(device_uuid, queue_copy)
        self.emit_status(device_uuid)
        return item

    def get_queue(self, device_uuid: str) -> List[Dict]:
        with self._lock:
            return list(getattr(self.rt(device_uuid), "task_queue", []))

    def cancel_queue_item(self, device_uuid: str, queue_id: str) -> bool:
        """Remove a specific item by queue_id. Returns True if found and removed."""
        with self._lock:
            r = self.rt(device_uuid)
            original = list(r.task_queue)
            r.task_queue = [item for item in r.task_queue if item.get("queue_id") != queue_id]
            removed = len(r.task_queue) != len(original)
            queue_copy = list(r.task_queue)
        if removed:
            self._persist_task_queue(device_uuid, queue_copy)
            self.emit_status(device_uuid)
        return removed

    def clear_queue(self, device_uuid: str):
        """Clear all queued tasks for this device."""
        with self._lock:
            r = self.rt(device_uuid)
            r.task_queue = []
        self._delete_task_queue(device_uuid)
        self.emit_status(device_uuid)

    def _persist_task_queue(self, device_uuid: str, queue: List[Dict]):
        db = SessionLocal()
        try:
            k = f"task_queue:{device_uuid}"
            val = json.dumps(queue, ensure_ascii=False)
            setting = db.query(BotSetting).filter(BotSetting.setting_key == k).first()
            if setting:
                setting.setting_value = val
                setting.updated_at = utc_now_naive()
            else:
                db.add(BotSetting(setting_key=k, setting_value=val, updated_at=utc_now_naive()))
            db.commit()
        except Exception as e:
            logger.debug(f"Failed to persist task queue for {device_uuid}: {e}")
            db.rollback()
        finally:
            db.close()

    def _delete_task_queue(self, device_uuid: str):
        db = SessionLocal()
        try:
            k = f"task_queue:{device_uuid}"
            db.query(BotSetting).filter(BotSetting.setting_key == k).delete()
            db.commit()
        except Exception as e:
            logger.debug(f"Failed to delete task queue for {device_uuid}: {e}")
            db.rollback()
        finally:
            db.close()

    def _load_all_task_queues(self):
        db = SessionLocal()
        try:
            settings_rows = db.query(BotSetting).filter(BotSetting.setting_key.like("task_queue:%")).all()
            for s in settings_rows:
                dev_uuid = s.setting_key.split("task_queue:", 1)[1]
                if s.setting_value:
                    try:
                        q = json.loads(s.setting_value)
                        if isinstance(q, list):
                            with self._lock:
                                r = self.rt(dev_uuid)
                                r.task_queue = q
                    except Exception as err:
                        logger.warning(f"Failed to parse task queue for {dev_uuid}: {err}")
        except Exception as e:
            logger.debug(f"Failed to load task queues: {e}")
        finally:
            db.close()

    def _persist_task_intent(self, device_uuid: str, intent: TaskIntent):
        db = SessionLocal()
        try:
            k = f"task_intent:{device_uuid}"
            val = json.dumps(intent.to_dict())
            setting = db.query(BotSetting).filter(BotSetting.setting_key == k).first()
            if setting:
                setting.setting_value = val
                setting.updated_at = utc_now_naive()
            else:
                db.add(BotSetting(setting_key=k, setting_value=val, updated_at=utc_now_naive()))
            db.commit()
        except Exception as e:
            logger.debug(f"Failed to persist task intent for {device_uuid}: {e}")
            db.rollback()
        finally:
            db.close()

    def _delete_task_intent(self, device_uuid: str):
        db = SessionLocal()
        try:
            k = f"task_intent:{device_uuid}"
            db.query(BotSetting).filter(BotSetting.setting_key == k).delete()
            db.commit()
        except Exception as e:
            logger.debug(f"Failed to delete task intent for {device_uuid}: {e}")
            db.rollback()
        finally:
            db.close()

    def _load_all_task_intents(self):
        db = SessionLocal()
        try:
            settings_rows = db.query(BotSetting).filter(BotSetting.setting_key.like("task_intent:%")).all()
            for s in settings_rows:
                dev_uuid = s.setting_key.split("task_intent:", 1)[1]
                if s.setting_value:
                    try:
                        d = json.loads(s.setting_value)
                        intent = TaskIntent.from_dict(d)
                        with self._lock:
                            r = self.rt(dev_uuid)
                            r.task_intent = intent
                            r.stop_mode = intent.stop_mode
                            r.stop_limit = intent.stop_limit
                            r.session_completed_count = intent.completed_count
                    except Exception as err:
                        logger.warning(f"Failed to parse task intent for {dev_uuid}: {err}")
        except Exception as e:
            logger.debug(f"Failed to load task intents: {e}")
        finally:
            db.close()

    def name_of(self, device_uuid: str) -> str:
        from backend.event_log import device_name
        return device_name(device_uuid) or device_uuid[:8]

    # ------------------------------------------------------------------ queries / DTO
    @staticmethod
    def _stats(db) -> Dict[str, Dict[str, int]]:
        nonblack = db.execute(text("SELECT COUNT(*) FROM redeem_keys WHERE is_blacklisted=0")).scalar() or 0
        out: Dict[str, Dict[str, int]] = {}
        rows = db.execute(text(
            "SELECT c.device_uuid, "
            "SUM(CASE WHEN rk.is_blacklisted=0 THEN 1 ELSE 0 END) AS claimed_nonblack, "
            "SUM(CASE WHEN c.status='USED' THEN 1 ELSE 0 END) AS used, "
            "SUM(CASE WHEN c.status='INVALID' THEN 1 ELSE 0 END) AS invalid "
            "FROM device_key_claims c JOIN redeem_keys rk ON rk.id=c.key_id GROUP BY c.device_uuid")).all()
        for r in rows:
            out[r[0]] = {"eligible": nonblack - int(r[1] or 0), "used": int(r[2] or 0), "invalid": int(r[3] or 0)}
        out["__nonblack__"] = {"n": nonblack}
        return out

    def to_dto(self, inst: Instance, stats: Optional[dict] = None) -> Dict[str, Any]:
        stats = stats or {}
        s = stats.get(inst.uuid) or {"eligible": (stats.get("__nonblack__") or {}).get("n", 0), "used": 0, "invalid": 0}
        r = self.rt(inst.uuid)
        dto = self.status_payload(inst.uuid)
        conn_type = (inst.connection_type or ("tcp" if adb_manager.is_tcp_serial(inst.address) else "usb")).lower()
        dto.update({
            "uuid": inst.uuid, "name": inst.name,
            "connection_type": conn_type,
            "adb_serial": inst.address,
            "ip": inst.ip, "port": inst.port,
            "address": inst.address, "live_preview_enabled": bool(inst.live_preview_enabled),
            "nusuk_installed": inst.nusuk_installed, "anchor_planted": bool(inst.anchor_planted),
            "is_legacy_primary": bool(inst.is_legacy_primary),
            "last_seen_at": (r.last_seen_at or inst.last_seen_at).isoformat() if (r.last_seen_at or inst.last_seen_at) else None,
            "eligible_keys": s["eligible"], "used_keys": s["used"], "invalid_keys": s["invalid"],
        })
        return dto

    def snapshot(self) -> List[Dict[str, Any]]:
        db = SessionLocal()
        try:
            stats = self._stats(db)
            items = db.query(Instance).filter(Instance.is_deleted == False).order_by(Instance.created_at.asc()).all()  # noqa: E712
            return [self.to_dto(i, stats) for i in items]
        finally:
            db.close()

    def emit_device_list(self):
        telemetry_manager.emit_sync("device_list_update", {"instances": self.snapshot()})

    def active_instances(self) -> List[Instance]:
        db = SessionLocal()
        try:
            items = db.query(Instance).filter(Instance.is_deleted == False).order_by(Instance.created_at.asc()).all()  # noqa: E712
            db.expunge_all()
            return items
        finally:
            db.close()

    def get_instance(self, device_uuid: str, include_deleted: bool = False) -> Optional[Instance]:
        db = SessionLocal()
        try:
            q = db.query(Instance).filter(Instance.uuid == device_uuid)
            if not include_deleted:
                q = q.filter(Instance.is_deleted == False)  # noqa: E712
            inst = q.first()
            if inst:
                db.expunge(inst)
            return inst
        finally:
            db.close()

    # ------------------------------------------------------------------ lifecycle
    def load(self):
        for inst in self.active_instances():
            set_device_name(inst.uuid, inst.name)
            with self._lock:
                self.runtime[inst.uuid] = Runtime(last_app_keys=inst.last_app_keys)
        self._load_all_task_intents()
        self._load_all_task_queues()

    def start_watchdog(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._watchdog_loop, name="conn-watchdog", daemon=True)
        self._thread.start()

    def stop_watchdog(self):
        self._stop.set()

    # ------------------------------------------------------------------ validation / registration
    @staticmethod
    def validate_address(ip: str, port: int) -> str:
        ip = (ip or "").strip()
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            raise ValueError("Format IP Address tidak valid.")
        if not isinstance(port, int) or not (1 <= port <= 65535):
            raise ValueError("Port harus berada di rentang 1-65535.")
        return f"{ip}:{port}"

    @classmethod
    def resolve_connection(
        cls,
        connection_type: Optional[str] = "tcp",
        ip: Optional[str] = None,
        port: Optional[int] = None,
        adb_serial: Optional[str] = None,
    ) -> Tuple[str, Optional[str], Optional[int], str]:
        """Normalizes & validates (connection_type, ip, port, adb_serial) across tcp/usb/wireless."""
        ct = (connection_type or "tcp").strip().lower()
        if ct not in VALID_CONN_TYPES:
            raise ValueError("Tipe koneksi tidak valid. Gunakan 'tcp', 'usb', atau 'wireless'.")

        if ct == "usb":
            serial = (adb_serial or "").strip()
            if not serial or not _SERIAL_SAFE_RE.match(serial):
                raise ValueError("Serial perangkat USB tidak valid.")
            if adb_manager.is_tcp_serial(serial):
                raise ValueError("Serial berformat IP:Port tidak cocok untuk mode USB. Gunakan tab IP & Port atau Wireless.")
            return "usb", None, None, serial

        # tcp or wireless: accept either ip+port or ip:port inside adb_serial
        ip_clean = (ip or "").strip()
        port_val = port
        if not ip_clean and adb_serial and adb_manager.is_tcp_serial(adb_serial):
            ip_part, port_part = adb_serial.strip().rsplit(":", 1)
            ip_clean = ip_part
            try:
                port_val = int(port_part)
            except ValueError:
                port_val = None
        if port_val is None:
            raise ValueError("Port wajib diisi untuk koneksi TCP/Wireless.")
        target = cls.validate_address(ip_clean, int(port_val))
        return ct, ip_clean, int(port_val), target

    def _active_by_serial(self, db, serial: str, exclude_uuid: Optional[str] = None) -> Optional[Instance]:
        rows = db.query(Instance).filter(Instance.is_deleted == False).all()  # noqa: E712
        for row in rows:
            if exclude_uuid and row.uuid == exclude_uuid:
                continue
            if row.address == serial:
                return row
        return None

    def discover_devices(self) -> List[Dict[str, Any]]:
        """Scans currently attached ADB devices (`adb devices -l`) and enriches with DB registration status."""
        discovered = adb_manager.discover_devices()
        db = SessionLocal()
        try:
            active_rows = db.query(Instance).filter(Instance.is_deleted == False).all()  # noqa: E712
            all_rows_by_uuid = {r.uuid: r for r in db.query(Instance).all()}
            active_by_serial = {r.address: r for r in active_rows}
        finally:
            db.close()

        enriched: List[Dict[str, Any]] = []
        for dev in discovered:
            serial = dev["adb_serial"]
            state = dev["state"]
            reg = active_by_serial.get(serial)
            known = None
            hint = ""

            if reg:
                known = {
                    "uuid": reg.uuid, "name": reg.name,
                    "is_deleted": False, "address": reg.address,
                    "connection_type": reg.connection_type or "tcp",
                }
            elif state == "device":
                anchor = adb_manager.read_anchor(serial)
                if anchor and anchor in all_rows_by_uuid:
                    row = all_rows_by_uuid[anchor]
                    known = {
                        "uuid": row.uuid, "name": row.name,
                        "is_deleted": bool(row.is_deleted), "address": row.address,
                        "connection_type": row.connection_type or "tcp",
                    }

            if state == "unauthorized":
                hint = "Ketuk 'Allow / Izinkan USB Debugging' di layar HP Anda, lalu scan ulang."
            elif state == "offline":
                hint = "Perangkat terdeteksi offline di ADB. Cabut & colok ulang kabel USB atau aktifkan ulang USB Debugging."
            elif reg:
                hint = f"Sudah terdaftar sebagai '{reg.name}'."
            elif known and not known["is_deleted"]:
                hint = f"UUID dikenali milik '{known['name']}' ({known['address']}). Memilih ini akan memperbarui koneksi tanpa duplikasi."
            elif known and known["is_deleted"]:
                hint = f"Perangkat arsip '{known['name']}' terdeteksi — histori akan dipulihkan."
            else:
                hint = "Siap dicek aplikasi Nusuk & ditambahkan."

            enriched.append({
                **dev,
                "already_registered": bool(reg),
                "known_device": known,
                "hint": hint,
            })
        return enriched

    def pair_wireless(self, ip: str, pairing_port: int, pairing_code: str) -> Dict[str, Any]:
        """Pairs an Android 11+ device via Wireless Debugging (`adb pair`) and probes mDNS connect port."""
        self.validate_address(ip, int(pairing_port))
        code_clean = (pairing_code or "").strip()
        if not code_clean:
            raise ValueError("Pairing Code tidak boleh kosong.")

        ok, msg = adb_manager.pair_wireless(ip.strip(), int(pairing_port), code_clean, timeout=15)
        suggested_port = None
        mdns_hits: List[Dict[str, Any]] = []
        if ok:
            time.sleep(0.4)
            mdns_hits = adb_manager.discover_mdns_connect_ports(ip.strip())
            if mdns_hits:
                suggested_port = mdns_hits[0]["port"]
            else:
                # Also check if Android already opened a connect endpoint visible in `adb devices`
                for serial, st in adb_manager.device_states().items():
                    if st == "device" and serial.startswith(f"{ip.strip()}:"):
                        try:
                            p = int(serial.rsplit(":", 1)[1])
                            if p != int(pairing_port):
                                suggested_port = p
                                break
                        except ValueError:
                            pass
        return {
            "success": ok,
            "message": msg,
            "ip": ip.strip(),
            "suggested_connect_port": suggested_port,
            "mdns_services": mdns_hits,
        }

    def test_connection(
        self,
        ip: Optional[str] = None,
        port: Optional[int] = None,
        connection_type: str = "tcp",
        adb_serial: Optional[str] = None,
    ) -> Dict[str, Any]:
        ct, ip_clean, port_val, target = self.resolve_connection(connection_type, ip, port, adb_serial)
        res: Dict[str, Any] = {
            "connection_type": ct, "adb_serial": target, "ip": ip_clean, "port": port_val,
            "adb_ok": False, "adb_state": None, "model": None,
            "nusuk_installed": None, "message": "", "already_registered": False,
            "known_device": None, "relink_existing": False, "clone_conflict": False,
            "can_save": False, "_anchor": None,
        }

        if ct == "usb":
            st = adb_manager.device_states().get(target)
            res["adb_state"] = st
            if st == "unauthorized":
                res["message"] = (
                    "Perangkat USB terdeteksi namun berstatus UNAUTHORIZED. "
                    "Ketuk 'Allow / Izinkan USB Debugging' di layar HP Anda, lalu coba lagi."
                )
                return res
            if st != "device":
                res["message"] = (
                    f"Perangkat USB '{target}' tidak siap di ADB (status: {st or 'tidak ditemukan'}). "
                    "Pastikan kabel terhubung dan USB Debugging aktif."
                )
                return res
        else:
            if not adb_manager.ensure_connected(target, connection_type=ct):
                res["message"] = (
                    "Koneksi ADB gagal: Pastikan IP, Port, dan ADB / Wireless Debugging aktif "
                    "(dan sudah melakukan Pair untuk Android 11+)."
                )
                return res
            res["adb_state"] = "device"

        res["adb_ok"] = True
        res["model"] = adb_manager.get_device_model(target)
        res["nusuk_installed"] = adb_manager.is_package_installed(target)
        anchor = adb_manager.read_anchor(target)
        res["_anchor"] = anchor

        db = SessionLocal()
        try:
            same_serial = self._active_by_serial(db, target)
            if same_serial:
                res["already_registered"] = True
                res["known_device"] = {
                    "uuid": same_serial.uuid, "name": same_serial.name,
                    "is_deleted": False, "address": same_serial.address,
                    "connection_type": same_serial.connection_type or "tcp",
                }
            if anchor:
                row = db.query(Instance).filter(Instance.uuid == anchor).first()
                if row:
                    res["known_device"] = {
                        "uuid": row.uuid, "name": row.name,
                        "is_deleted": bool(row.is_deleted), "address": row.address,
                        "connection_type": row.connection_type or "tcp",
                    }
                    if not row.is_deleted:
                        if row.address == target:
                            res["already_registered"] = True
                        else:
                            other = row.address
                            if adb_manager.is_ready(other) and adb_manager.read_anchor(other) == anchor:
                                res["clone_conflict"] = True
                            else:
                                res["relink_existing"] = True
        finally:
            db.close()

        if res["already_registered"]:
            res["message"] = "Perangkat ini sudah terdaftar dan aktif di dashboard."
        elif res["nusuk_installed"] is False:
            res["message"] = ("ADB Terhubung, namun Aplikasi Nusuk (com.moh.nusukapp) TIDAK DITEMUKAN "
                              "di perangkat ini.")
        elif res["clone_conflict"]:
            res["message"] = (
                "UUID perangkat ini sedang aktif bersamaan di koneksi/alamat berbeda "
                "(kemungkinan hasil clone atau masih terhubung via USB & Wireless bersamaan). "
                "Putuskan koneksi lama atau centang buat UUID baru."
            )
            res["can_save"] = True
        elif res["known_device"] and res["known_device"]["is_deleted"]:
            res["message"] = ("Perangkat lama terdeteksi! Seluruh histori kuis, log, dan riwayat klaim key "
                              "sebelumnya akan dilanjutkan.")
            res["can_save"] = True
        elif res["relink_existing"] and res["known_device"]:
            old_ct = (res["known_device"].get("connection_type") or "tcp").upper()
            res["message"] = (
                f"Perangkat '{res['known_device']['name']}' dikenali via Device UUID! "
                f"Koneksi akan diperbarui ({old_ct} -> {ct.upper()}: {target}) tanpa duplikasi dan seluruh histori tetap utuh."
            )
            res["can_save"] = True
        else:
            res["message"] = "Terhubung & Aplikasi Nusuk Terdeteksi."
            res["can_save"] = True
        res.pop("_anchor", None)
        return res

    def add_instance(
        self,
        name: Optional[str] = None,
        ip: Optional[str] = None,
        port: Optional[int] = None,
        connection_type: str = "tcp",
        adb_serial: Optional[str] = None,
        force_new_uuid: bool = False,
        bind_to_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        ct, ip_clean, port_val, target = self.resolve_connection(connection_type, ip, port, adb_serial)

        clean_bind_uuid = (bind_to_uuid or "").strip().lower() or None
        if clean_bind_uuid:
            if not adb_manager.is_valid_uuid(clean_bind_uuid):
                raise ValueError(f"Format UUID migrasi tidak valid: '{bind_to_uuid}'")
            db_check = SessionLocal()
            try:
                target_row = db_check.query(Instance).filter(Instance.uuid == clean_bind_uuid).first()
                if not target_row:
                    raise ValueError(f"Target perangkat lama (UUID: {clean_bind_uuid}) tidak ditemukan di database.")
            finally:
                db_check.close()
            if ct != "usb":
                adb_manager.ensure_connected(target, connection_type=ct)
            # Force overwrite device UUID anchor before reading/verifying anchor UUID
            adb_manager.shell(target, f"echo -n {clean_bind_uuid} > /sdcard/.nusuk_device_uuid", timeout=8)

        info = self.test_connection(ip=ip_clean, port=port_val, connection_type=ct, adb_serial=target)
        if not info["adb_ok"]:
            raise PermissionError(info["message"])
        if info["already_registered"]:
            raise FileExistsError(info["message"])
        if info["clone_conflict"] and not force_new_uuid:
            raise FileExistsError(info["message"] + " Ulangi dengan opsi 'buat UUID baru'.")

        anchor = adb_manager.read_anchor(target)
        restored = False
        relinked = False
        db = SessionLocal()
        try:
            row = None
            if anchor and not info["clone_conflict"]:
                row = db.query(Instance).filter(Instance.uuid == anchor).first()
            if row:
                if row.is_deleted:
                    row.is_deleted = False
                    row.deleted_at = None
                    restored = True
                else:
                    relinked = True
                row.connection_type = ct
                row.adb_serial = target
                row.ip = ip_clean
                row.port = port_val
                if name and name.strip():
                    row.name = name.strip()[:64]
                row.nusuk_installed = bool(info.get("nusuk_installed"))
                row.anchor_planted = True
                dev_uuid = row.uuid
            else:
                dev_uuid = new_uuid()
                default_alias = (name or "").strip()[:64]
                if not default_alias and ct in ("usb", "wireless") and info.get("model"):
                    default_alias = str(info["model"]).strip()[:64]
                row = Instance(
                    uuid=dev_uuid,
                    name=default_alias or self._auto_name(db),
                    connection_type=ct,
                    adb_serial=target,
                    ip=ip_clean,
                    port=port_val,
                    live_preview_enabled=True,
                    nusuk_installed=bool(info.get("nusuk_installed")),
                    anchor_planted=False,
                    is_legacy_primary=False,
                    created_at=utc_now_naive(),
                )
                db.add(row)
                db.flush()
                if adb_manager.write_anchor(target, dev_uuid):
                    row.anchor_planted = True
            db.commit()
            db.refresh(row)
            set_device_name(row.uuid, row.name)
            with self._lock:
                self.runtime[row.uuid] = Runtime(last_app_keys=row.last_app_keys)
            self.set_runtime(row.uuid, status="IDLE", current_step="Siap digunakan", offline_logged=False,
                             last_seen_at=utc_now_naive())
            stats = self._stats(db)
            dto = self.to_dto(row, stats)
            inst_name = row.name
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        if restored:
            log_msg = f"Perangkat lama dipulihkan ({ct.upper()}): {inst_name} ({target})"
        elif relinked:
            log_msg = f"Koneksi perangkat diperbarui via Device UUID ({ct.upper()}): {inst_name} ({target})"
        else:
            log_msg = f"Instance baru didaftarkan ({ct.upper()}): {inst_name} ({target})"
        log_event(LogSeverity.INFO, "INSTANCE", log_msg, dto["uuid"])
        self.emit_device_list()
        return {"instance": dto, "restored": restored, "relinked": relinked}

    @staticmethod
    def _auto_name(db) -> str:
        n = db.query(Instance).count() + 1
        names = {r[0] for r in db.query(Instance.name).all()}
        while f"Instance-{n:02d}" in names:
            n += 1
        return f"Instance-{n:02d}"

    def update_instance(
        self,
        device_uuid: str,
        name: Optional[str] = None,
        ip: Optional[str] = None,
        port: Optional[int] = None,
        connection_type: Optional[str] = None,
        adb_serial: Optional[str] = None,
        live_preview_enabled: Optional[bool] = None,
        worker_active: bool = False,
    ) -> Dict[str, Any]:
        db = SessionLocal()
        try:
            row = db.query(Instance).filter(Instance.uuid == device_uuid, Instance.is_deleted == False).first()  # noqa: E712
            if not row:
                raise LookupError("Instance tidak ditemukan.")
            addr_changed = False
            if name is not None and name.strip():
                row.name = name.strip()[:64]
                set_device_name(row.uuid, row.name)

            target_ct = (connection_type or row.connection_type or "tcp").strip().lower()
            if ip is not None or port is not None or adb_serial is not None or connection_type is not None:
                if target_ct == "usb":
                    new_serial = (adb_serial if adb_serial is not None else row.adb_serial or "").strip()
                    ct, new_ip, new_port, target = self.resolve_connection("usb", None, None, new_serial)
                else:
                    new_ip_in = (ip if ip is not None else row.ip or "").strip()
                    new_port_in = port if port is not None else (row.port or 5555)
                    ct, new_ip, new_port, target = self.resolve_connection(target_ct, new_ip_in, new_port_in, adb_serial)

                if target != row.address or ct != (row.connection_type or "tcp"):
                    if worker_active:
                        raise FileExistsError("Hentikan bot pada device ini sebelum mengubah alamat/koneksi.")
                    if self._active_by_serial(db, target, exclude_uuid=row.uuid):
                        raise FileExistsError("Alamat / Serial ADB tersebut sudah dipakai instance aktif lain.")
                    row.connection_type = ct
                    row.adb_serial = target
                    row.ip = new_ip
                    row.port = new_port
                    addr_changed = True

            preview_changed = False
            if live_preview_enabled is not None and bool(live_preview_enabled) != bool(row.live_preview_enabled):
                row.live_preview_enabled = bool(live_preview_enabled)
                preview_changed = True
            db.commit()
            db.refresh(row)
            if addr_changed:
                self.set_runtime(row.uuid, status="OFFLINE", offline_logged=False, next_probe_at=0.0, fail_count=0,
                                 current_step="Koneksi diubah - memverifikasi perangkat...")
            dto = self.to_dto(row, self._stats(db))
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        if preview_changed:
            telemetry_manager.emit_sync("preview_setting_update",
                                        {"device_uuid": device_uuid, "live_preview_enabled": dto["live_preview_enabled"]})
        self.emit_device_list()
        return dto

    def soft_delete(self, device_uuid: str):
        db = SessionLocal()
        try:
            row = db.query(Instance).filter(Instance.uuid == device_uuid, Instance.is_deleted == False).first()  # noqa: E712
            if not row:
                raise LookupError("Instance tidak ditemukan.")
            row.is_deleted = True
            row.deleted_at = utc_now_naive()
            name, addr = row.name, row.address
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        with self._lock:
            self.runtime.pop(device_uuid, None)
        log_event(LogSeverity.INFO, "INSTANCE",
                  f"Instance '{name}' ({addr}) dihapus (soft-delete). Riwayat data tetap tersimpan.", device_uuid)
        self.emit_device_list()

    def archived(self) -> List[Dict[str, Any]]:
        db = SessionLocal()
        try:
            rows = db.query(Instance).filter(Instance.is_deleted == True).order_by(Instance.deleted_at.desc()).all()  # noqa: E712
            return [{
                "uuid": r.uuid, "name": r.name,
                "connection_type": r.connection_type or "tcp",
                "adb_serial": r.address, "address": r.address,
                "ip": r.ip, "port": r.port,
                "deleted_at": r.deleted_at.isoformat() if r.deleted_at else None,
            } for r in rows]
        finally:
            db.close()

    def persist_app_keys(self, device_uuid: str, keys: int):
        db = SessionLocal()
        try:
            db.query(Instance).filter(Instance.uuid == device_uuid).update({"last_app_keys": keys})
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    # ------------------------------------------------------------------ coming online / watchdog
    def come_online(self, device_uuid: str, address: str, is_legacy: bool = False) -> bool:
        """Verify/plant the UUID anchor + Nusuk app, then flip OFFLINE/REBOOTING -> IDLE (one INFO log)."""
        anchor = adb_manager.read_anchor(address)
        db = SessionLocal()
        try:
            row = db.query(Instance).filter(Instance.uuid == device_uuid).first()
            if not row:
                return False
            if anchor is None:
                if adb_manager.write_anchor(address, device_uuid):
                    row.anchor_planted = True
                    log_event(LogSeverity.INFO, "INSTANCE",
                              f"Jangkar Device UUID ditanam ke emulator {address}.", device_uuid)
            elif anchor == device_uuid.lower():
                row.anchor_planted = True
            else:
                r = self.rt(device_uuid)
                if not r.identity_warned:
                    log_event(LogSeverity.WARNING, "INSTANCE",
                              f"Perangkat di {address} membawa UUID lain ({anchor[:8]}...). UUID tidak ditimpa; "
                              f"periksa IP:Port instance ini.", device_uuid)
                self.set_runtime(device_uuid, status="ERROR", identity_warned=True, mode=None,
                                 current_step="Identitas perangkat tidak cocok dengan alamat ini")
                db.commit()
                return False
            row.nusuk_installed = adb_manager.is_package_installed(address)
            row.last_seen_at = utc_now_naive()
            db.commit()
            installed = row.nusuk_installed
        finally:
            db.close()
        self.set_runtime(device_uuid, status="IDLE", mode=None, offline_logged=False, fail_count=0,
                         reboot_started=None, reboot_saw_down=False, identity_warned=False,
                         current_step="Siap digunakan" if installed else "Aplikasi Nusuk tidak terdeteksi",
                         last_seen_at=utc_now_naive())
        log_event(LogSeverity.INFO, "CONNECTION",
                  f"Instance [{self.name_of(device_uuid)}] kembali ONLINE dan siap digunakan", device_uuid)
        self.emit_device_list()
        r = self.rt(device_uuid)
        if getattr(r, "task_intent", None) and installed:
            from backend.orchestrator import orchestrator
            self._probe_pool.submit(orchestrator.trigger_auto_resume, device_uuid)
        return True

    def reconnect_now(self, device_uuid: str) -> bool:
        inst = self.get_instance(device_uuid)
        if not inst:
            return False
        ct = (inst.connection_type or "tcp").lower()
        if adb_manager.ensure_connected(inst.address, connection_type=ct):
            st = self.rt(device_uuid).status
            if st in ("OFFLINE", "ERROR", "REBOOTING"):
                return self.come_online(device_uuid, inst.address, inst.is_legacy_primary)
            else:
                r = self.rt(device_uuid)
                if getattr(r, "task_intent", None) and st == "IDLE":
                    from backend.orchestrator import orchestrator
                    self._probe_pool.submit(orchestrator.trigger_auto_resume, device_uuid)
            return True
        self.set_runtime(device_uuid, next_probe_at=0.0)
        return False

    def reconnect_all_offline(self) -> int:
        n = 0
        for inst in self.active_instances():
            if self.rt(inst.uuid).status == "OFFLINE":
                self.set_runtime(inst.uuid, emit=False, next_probe_at=0.0)
                n += 1
        return n

    def _probe_offline(self, inst: Instance, prechecked_ready: Optional[bool] = None):
        addr = inst.address
        ct = (inst.connection_type or "tcp").lower()
        is_usb = (ct == "usb") or (not adb_manager.is_tcp_serial(addr))
        r = self.rt(inst.uuid)
        if is_usb:
            # USB devices NEVER use `adb connect`; rely purely on `adb devices` state.
            ready = bool(prechecked_ready) if prechecked_ready is not None else adb_manager.is_ready(addr)
        else:
            ready = adb_manager.ensure_connected(addr, connection_type=ct)
        if ready:
            self.come_online(inst.uuid, addr, inst.is_legacy_primary)
        else:
            interval = max(3, int(settings.watchdog_interval_seconds))
            r.fail_count += 1
            r.next_probe_at = time.time() + (interval if is_usb else min(30, interval * (1 + r.fail_count // 3)))
            if not r.offline_logged:
                r.offline_logged = True
                hint = (
                    "menunggu kabel USB terhubung kembali..."
                    if is_usb
                    else "Mencoba menyambung ulang di latar belakang..."
                )
                log_event(LogSeverity.WARNING, "CONNECTION",
                          f"Instance [{inst.name}] tidak dapat dijangkau - status OFFLINE. {hint}", inst.uuid)

    def _watchdog_loop(self):
        logger.info("Connection watchdog started")
        while not self._stop.is_set():
            try:
                self._watchdog_tick()
            except Exception as e:
                logger.error(f"watchdog tick error: {e}", exc_info=True)
            self._stop.wait(max(3, int(settings.watchdog_interval_seconds)))

    def _watchdog_tick(self):
        instances = self.active_instances()
        if not instances:
            return
        states = adb_manager.device_states()
        now = time.time()
        futures = []
        for inst in instances:
            r = self.rt(inst.uuid)
            st = r.status
            ct = (inst.connection_type or "tcp").lower()
            is_usb = (ct == "usb") or (not adb_manager.is_tcp_serial(inst.address))
            ready = states.get(inst.address) == "device"
            if st in ("RUNNING", "RECOVERING", "STOPPING"):
                continue  # owned by its worker
            if st == "REBOOTING":
                if not ready:
                    r.reboot_saw_down = True
                    if r.reboot_started and now - r.reboot_started > 300:
                        self.mark_offline(inst.uuid, "tidak kembali setelah reboot (300 dtk)")
                elif r.reboot_saw_down or (r.reboot_started and now - r.reboot_started > 25):
                    code, out, _ = adb_manager.shell(inst.address, "getprop sys.boot_completed", timeout=6)
                    if code == 0 and out.strip() == "1":
                        futures.append(self._probe_pool.submit(self.come_online, inst.uuid, inst.address, inst.is_legacy_primary))
                continue
            if st in ("IDLE", "ERROR"):
                if ready:
                    r.last_seen_at = utc_now_naive()
                    r.missed_heartbeats = 0
                else:
                    if is_usb:
                        # USB cable unplugged: never call `adb connect`, transition smoothly to OFFLINE
                        r.missed_heartbeats = getattr(r, "missed_heartbeats", 0) + 1
                        if r.missed_heartbeats >= 2:
                            self.mark_offline(inst.uuid, "kabel USB terputus / tidak terdeteksi")
                    else:
                        # TCP/Wireless: try ensure_connected before declaring offline
                        reconnected = adb_manager.ensure_connected(inst.address, retries=1, connection_type=ct)
                        if reconnected:
                            r.last_seen_at = utc_now_naive()
                            r.missed_heartbeats = 0
                        else:
                            r.missed_heartbeats = getattr(r, "missed_heartbeats", 0) + 1
                            if r.missed_heartbeats >= 3:
                                self.mark_offline(inst.uuid)
                continue
            if st == "OFFLINE":
                if is_usb and ready:
                    # Physical USB device plugged back in -> immediately restore to IDLE!
                    futures.append(self._probe_pool.submit(self.come_online, inst.uuid, inst.address, inst.is_legacy_primary))
                elif now >= r.next_probe_at:
                    futures.append(self._probe_pool.submit(self._probe_offline, inst, ready if is_usb else None))
        for f in futures:
            try:
                f.result(timeout=60)
            except Exception as e:
                logger.debug(f"probe error: {e}")


instance_manager = InstanceManager()
