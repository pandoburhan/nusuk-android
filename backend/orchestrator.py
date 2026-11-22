"""Engine Orchestrator: registry of InstanceWorkers, bulk executors & direct mode switching."""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple, Any, Set

from backend.config import settings
from backend.models import LogSeverity
from backend.adb_manager import adb_manager
from backend.event_log import log_event
from backend.instance_manager import instance_manager
from backend.instance_worker import InstanceWorker
from backend.redroid_manager import redroid_manager
from backend import key_service

logger = logging.getLogger(__name__)

MODES = ("answer_only", "redeem_only", "auto")
AI_MODES = ("answer_only", "auto")
ACTIVE_STATES = ("RUNNING", "RECOVERING")
QUIZ_MODES = ("answer_only", "auto")  # modes that use daily-limited quiz system


class Orchestrator:
    def __init__(self):
        self.workers: Dict[str, InstanceWorker] = {}
        self._lock = threading.RLock()
        self._device_locks: Dict[str, threading.Lock] = {}
        self._switching: Set[str] = set()
        self._switch_cancelled: Set[str] = set()
        self._pool = ThreadPoolExecutor(max_workers=12, thread_name_prefix="orch")

    # ------------------------------------------------------------ helpers
    def _dev_lock(self, device_uuid: str) -> threading.Lock:
        with self._lock:
            lk = self._device_locks.get(device_uuid)
            if lk is None:
                lk = threading.Lock()
                self._device_locks[device_uuid] = lk
            return lk

    def is_active(self, device_uuid: str) -> bool:
        w = self.workers.get(device_uuid)
        return bool(w and w.is_running)

    def trigger_auto_resume(self, device_uuid: str) -> bool:
        """
        Auto-resumes a pending task intent on `device_uuid` after reconnect, reboot,
        app restart, or recoverable transient offline/error.
        """
        intent = instance_manager.get_task_intent(device_uuid)
        if not intent:
            return False

        dev_lock = self._dev_lock(device_uuid)
        if not dev_lock.acquire(blocking=False):
            logger.debug(f"Auto-Resume skipped for {device_uuid}: device lock busy")
            return False

        try:
            w = self.workers.get(device_uuid)
            if w and w.is_running:
                return True

            inst = instance_manager.get_instance(device_uuid)
            if not inst:
                return False

            rt = instance_manager.rt(device_uuid)
            if rt.status in ("REBOOTING", "STOPPING", "OFFLINE"):
                return False

            # Prerequisites validation
            if intent.mode in AI_MODES and not settings.ai_api_key:
                log_event(LogSeverity.ERROR, "BOT_CONTROL",
                          f"Auto-Resume tertunda: AI API Key belum dikonfigurasi untuk mode '{intent.mode}'.", device_uuid)
                return False

            fail_reason = self._validate_target_mode_for_device(inst, intent.mode)
            if fail_reason:
                log_event(LogSeverity.WARNING, "BOT_CONTROL",
                          f"Auto-Resume tertunda: {fail_reason}.", device_uuid)
                return False

            new_worker = InstanceWorker(inst)
            with self._lock:
                self.workers[device_uuid] = new_worker

            ok = new_worker.start(
                intent.mode,
                stop_mode=intent.stop_mode,
                stop_limit=intent.stop_limit,
                initial_completed=intent.completed_count,
            )
            if ok:
                log_event(
                    LogSeverity.INFO,
                    "BOT_CONTROL",
                    f"Auto-Resume berhasil: melanjutkan tugas '{intent.mode}' "
                    f"(progres: {intent.completed_count}/{intent.stop_limit}).",
                    device_uuid,
                )
                return True
            return False
        finally:
            dev_lock.release()

    def is_switching(self, device_uuid: str) -> bool:
        with self._lock:
            return device_uuid in self._switching

    def _ref(self, inst) -> Dict[str, str]:
        return {"uuid": inst.uuid, "name": inst.name}

    def _summary(self, verb: str, started: list, skipped: list) -> str:
        msg = f"{len(started)} device {verb}"
        if skipped:
            msg += f", {len(skipped)} dilewati (" + "; ".join(f"{s['name']}: {s['reason']}" for s in skipped[:5]) + ")"
        return msg

    @staticmethod
    def _validate_target_mode_for_device(inst, mode: str) -> Optional[str]:
        """Per-device prerequisite check for `mode` (never stops any running worker)."""
        if inst.nusuk_installed is False:
            return "Aplikasi Nusuk tidak terdeteksi di perangkat ini"
        if mode == "redeem_only" and key_service.eligible_count(inst.uuid) == 0:
            return "Stok key eligible untuk device ini = 0"
        return None

    # ------------------------------------------------------------ start & direct mode switch
    def start_many(
        self,
        device_uuids: List[str],
        mode: str,
        device_stop_conditions: Optional[Any] = None,
        stop_mode: str = "limit",
        stop_limit: int = 100,
    ) -> Dict[str, Any]:
        if mode not in MODES:
            raise ValueError("Mode tidak valid. Gunakan answer_only, redeem_only, atau auto.")
        if mode in AI_MODES and not settings.ai_api_key:
            raise ValueError("Tidak dapat memulai: AI API Key belum dikonfigurasi di Settings.")

        states = adb_manager.device_states()
        started, switched, already_running, skipped = [], [], [], []

        # Normalize per-device stop conditions map
        cond_map = {}
        if isinstance(device_stop_conditions, dict):
            cond_map = device_stop_conditions
        elif isinstance(device_stop_conditions, list):
            for c in device_stop_conditions:
                if isinstance(c, dict) and "device_uuid" in c:
                    cond_map[c["device_uuid"]] = c

        for uid in dict.fromkeys(device_uuids):  # dedupe, preserve order
            inst = instance_manager.get_instance(uid)
            if not inst:
                skipped.append({"uuid": uid, "name": uid[:8], "reason": "Instance tidak ditemukan / sudah dihapus"})
                continue

            u_cond = cond_map.get(uid) or {}
            if isinstance(u_cond, dict):
                dev_stop_mode = u_cond.get("stop_mode", stop_mode) or "limit"
                dev_stop_limit = u_cond.get("stop_limit", stop_limit) or 100
            else:
                dev_stop_mode = getattr(u_cond, "stop_mode", stop_mode) or "limit"
                dev_stop_limit = getattr(u_cond, "stop_limit", stop_limit) or 100
            try:
                dev_stop_limit = int(dev_stop_limit)
                if dev_stop_limit < 1:
                    dev_stop_limit = 100
            except (ValueError, TypeError):
                dev_stop_limit = 100

            dev_lock = self._dev_lock(uid)
            if not dev_lock.acquire(blocking=False):
                skipped.append({**self._ref(inst), "reason": "Sedang proses pergantian mode / restart"})
                continue

            try:
                with self._lock:
                    if uid in self._switching:
                        skipped.append({**self._ref(inst), "reason": "Sedang proses pergantian mode"})
                        continue

                rt = instance_manager.rt(uid)
                w = self.workers.get(uid)
                worker_active = bool(w and w.is_running) or rt.status in ACTIVE_STATES

                if rt.status == "REBOOTING":
                    skipped.append({**self._ref(inst), "reason": "Sedang REBOOTING"})
                    continue
                if rt.status == "STOPPING":
                    skipped.append({**self._ref(inst), "reason": "Sedang STOPPING"})
                    continue
                if rt.status == "OFFLINE" or states.get(inst.address) != "device":
                    skipped.append({**self._ref(inst), "reason": "OFFLINE (tidak dapat dijangkau via ADB)"})
                    continue
                if rt.identity_warned:
                    skipped.append({**self._ref(inst), "reason": "Identitas perangkat tidak cocok dengan alamat ini"})
                    continue

                if worker_active:
                    current_mode = (w.active_mode if w and w.is_running else rt.mode)
                    # Case 1: already running in the exact same mode -> update stop condition if requested
                    if current_mode == mode:
                        if w and w.is_running:
                            w.update_stop_condition(dev_stop_mode, dev_stop_limit)
                        instance_manager.set_task_intent(
                            uid, mode, dev_stop_mode, dev_stop_limit,
                            completed_count=(w.session_completed_count if w else rt.session_completed_count),
                        )
                        already_running.append({**self._ref(inst), "mode": mode, "stop_mode": dev_stop_mode, "stop_limit": dev_stop_limit})
                        continue

                    # Case 2: different mode -> pre-switch validation BEFORE stopping current worker
                    fail_reason = self._validate_target_mode_for_device(inst, mode)
                    if fail_reason:
                        skipped.append({
                            **self._ref(inst),
                            "reason": f"Switch dibatalkan: {fail_reason} (mode '{current_mode}' tetap berjalan)",
                        })
                        continue

                    # Reserve switch slot synchronously so rapid repeated clicks cannot race
                    with self._lock:
                        self._switching.add(uid)
                        self._switch_cancelled.discard(uid)

                    # SWITCH clears the existing queue (operator explicitly chose a new immediate mode)
                    q_len = len(instance_manager.get_queue(uid))
                    if q_len > 0:
                        instance_manager.clear_queue(uid)
                        log_event(LogSeverity.INFO, "BOT_CONTROL",
                                  f"Queue {q_len} item dihapus karena operator memilih mode baru via SWITCH.", uid)

                    instance_manager.set_task_intent(
                        uid, mode, dev_stop_mode, dev_stop_limit, completed_count=0
                    )
                    instance_manager.set_runtime(
                        uid,
                        status="STOPPING",
                        mode=mode,
                        stop_mode=dev_stop_mode,
                        stop_limit=dev_stop_limit,
                        session_completed_count=0,
                        current_step=f"Switching mode {current_mode or '?'} -> {mode} (menghentikan worker lama)...",
                    )
                    self._pool.submit(self._execute_mode_switch, uid, current_mode, mode, dev_stop_mode, dev_stop_limit)
                    switched.append({**self._ref(inst), "from_mode": current_mode, "to_mode": mode})
                    continue

                # Case 3: IDLE or ERROR -> normal start
                fail_reason = self._validate_target_mode_for_device(inst, mode)
                if fail_reason:
                    skipped.append({**self._ref(inst), "reason": fail_reason})
                    continue

                instance_manager.set_task_intent(
                    uid, mode, dev_stop_mode, dev_stop_limit, completed_count=0
                )
                if rt.status == "ERROR":
                    instance_manager.set_runtime(uid, status="IDLE", emit=False)
                worker = InstanceWorker(inst)
                with self._lock:
                    self.workers[uid] = worker
                if worker.start(mode, stop_mode=dev_stop_mode, stop_limit=dev_stop_limit):
                    started.append(self._ref(inst))
                else:
                    instance_manager.clear_task_intent(uid, "Gagal memulai worker")
                    skipped.append({**self._ref(inst), "reason": "Gagal memulai worker"})
            finally:
                dev_lock.release()

        parts = []
        if started:
            parts.append(f"{len(started)} dijalankan")
        if switched:
            parts.append(f"{len(switched)} switch mode")
        if already_running:
            parts.append(f"{len(already_running)} tetap berjalan (mode sama)")
        if skipped:
            parts.append(f"{len(skipped)} dilewati")
        msg = ", ".join(parts) if parts else "Tidak ada device yang diproses"
        if skipped:
            msg += " (" + "; ".join(f"{s['name']}: {s['reason']}" for s in skipped[:5]) + ")"

        return {
            "success": bool(started or switched or already_running) or not skipped,
            "message": msg,
            "started": started,
            "switched": switched,
            "already_running": already_running,
            "skipped": skipped,
        }

    def _execute_mode_switch(self, uid: str, old_mode: Optional[str], target_mode: str,
                             stop_mode: str = "limit", stop_limit: int = 100):
        """Background per-device direct mode switch (non-blocking for HTTP API)."""
        dev_lock = self._dev_lock(uid)
        try:
            with dev_lock:
                if uid in self._switch_cancelled:
                    return

                inst = instance_manager.get_instance(uid)
                if not inst:
                    return

                # 1. Stop old worker cleanly without slow homepage navigation, trigger Safe Rollback, and join
                old_worker = self.workers.get(uid)
                if old_worker and old_worker.is_running:
                    old_worker.stop(
                        reason=f"Mode switch {old_mode or '?'} -> {target_mode}",
                        return_to_home=False,
                        for_switch=True,
                    )
                    if not old_worker.join(timeout=30.0):
                        msg = "Switch mode dibatalkan: worker lama tidak berhenti dalam 30 detik."
                        log_event(LogSeverity.ERROR, "BOT_CONTROL", msg, uid)
                        instance_manager.set_runtime(uid, status="ERROR", mode=None, current_step=msg)
                        return

                if uid in self._switch_cancelled:
                    instance_manager.set_runtime(uid, status="IDLE", mode=None, current_step="Switch mode dibatalkan")
                    return

                if not adb_manager.ensure_connected(inst.address, retries=2):
                    instance_manager.mark_offline(uid, "terputus saat switch mode")
                    return

                # 2. Log switch & directly force-restart Nusuk app for a clean initial state
                log_event(
                    LogSeverity.INFO,
                    "BOT_CONTROL",
                    f"Switching mode from '{old_mode or 'none'}' to '{target_mode}' — force restarting Nusuk app...",
                    uid,
                )
                instance_manager.set_runtime(
                    uid,
                    status="STOPPING",
                    mode=target_mode,
                    current_step=f"Switching mode to {target_mode} (restarting app)...",
                )
                adb_manager.clear_ram(inst.address)

                if uid in self._switch_cancelled:
                    instance_manager.set_runtime(uid, status="IDLE", mode=None, current_step="Switch mode dibatalkan")
                    return

                adb_manager.launch_app(inst.address)

                if uid in self._switch_cancelled:
                    instance_manager.set_runtime(uid, status="IDLE", mode=None, current_step="Switch mode dibatalkan")
                    return

                if not adb_manager.ensure_connected(inst.address, retries=2):
                    instance_manager.mark_offline(uid, "terputus setelah restart aplikasi saat switch mode")
                    return

                # 3. Launch new worker in target_mode
                new_worker = InstanceWorker(inst)
                with self._lock:
                    self.workers[uid] = new_worker
                if not new_worker.start(target_mode, stop_mode=stop_mode, stop_limit=stop_limit):
                    instance_manager.set_runtime(uid, status="ERROR", mode=None, current_step="Gagal memulai worker baru")
        except Exception as e:
            logger.exception(f"Mode switch failed for {uid}: {e}")
            log_event(LogSeverity.ERROR, "BOT_CONTROL", f"Gagal switch mode ke '{target_mode}': {e}", uid)
            instance_manager.set_runtime(uid, status="ERROR", mode=None, current_step=f"Switch mode gagal: {e}")
        finally:
            with self._lock:
                self._switching.discard(uid)
                self._switch_cancelled.discard(uid)

    # ------------------------------------------------------------ stop
    def stop_many(self, device_uuids: List[str], reason: str = "Operator clicked STOP in dashboard") -> Dict[str, Any]:
        started, skipped = [], []
        for uid in dict.fromkeys(device_uuids):
            inst = instance_manager.get_instance(uid)
            if not inst:
                skipped.append({"uuid": uid, "name": uid[:8], "reason": "Instance tidak ditemukan"})
                continue

            # Clear task intent AND queue so this device will not auto-resume or run any queued task
            instance_manager.clear_task_intent(uid, reason=reason)
            instance_manager.clear_queue(uid)

            switching = self.is_switching(uid)
            if switching:
                with self._lock:
                    self._switch_cancelled.add(uid)

            w = self.workers.get(uid)
            if w and w.is_running:
                w.stop(reason)
                started.append(self._ref(inst))
            elif switching:
                log_event(LogSeverity.INFO, "BOT_CONTROL", f"Stopping bot (membatalkan switch mode): {reason}", uid)
                started.append(self._ref(inst))
            else:
                skipped.append({**self._ref(inst), "reason": "Tidak sedang berjalan"})
        return {"success": True, "message": self._summary("dihentikan", started, skipped),
                "started": started, "skipped": skipped}

    def _stop_and_wait(self, device_uuid: str, reason: str, timeout: float = 60.0, return_to_home: bool = True) -> bool:
        w = self.workers.get(device_uuid)
        if w and w.is_running:
            w.stop(reason, return_to_home=return_to_home)
            return w.join(timeout)
        return True

    # ------------------------------------------------------------ queue management
    def queue_many(
        self,
        device_uuids: List[str],
        mode: str,
        device_stop_conditions: Optional[Any] = None,
        stop_mode: str = "limit",
        stop_limit: int = 100,
    ) -> Dict[str, Any]:
        """Enqueue a future task per device. Never interrupts the current running task."""
        if mode not in MODES:
            raise ValueError("Mode tidak valid. Gunakan answer_only, redeem_only, atau auto.")
        if mode in AI_MODES and not settings.ai_api_key:
            raise ValueError("Tidak dapat mengantrekan: AI API Key belum dikonfigurasi di Settings.")

        # Normalize per-device stop conditions
        cond_map: Dict = {}
        if isinstance(device_stop_conditions, dict):
            cond_map = device_stop_conditions
        elif isinstance(device_stop_conditions, list):
            for c in device_stop_conditions:
                if isinstance(c, dict) and "device_uuid" in c:
                    cond_map[c["device_uuid"]] = c

        queued, skipped = [], []

        for uid in dict.fromkeys(device_uuids):
            inst = instance_manager.get_instance(uid)
            if not inst:
                skipped.append({"uuid": uid, "name": uid[:8], "reason": "Instance tidak ditemukan / sudah dihapus"})
                continue

            u_cond = cond_map.get(uid) or {}
            dev_stop_mode = (u_cond.get("stop_mode") if isinstance(u_cond, dict) else getattr(u_cond, "stop_mode", None)) or stop_mode or "limit"
            dev_stop_limit = (u_cond.get("stop_limit") if isinstance(u_cond, dict) else getattr(u_cond, "stop_limit", None)) or stop_limit or 100
            try:
                dev_stop_limit = int(dev_stop_limit)
                if dev_stop_limit < 1:
                    dev_stop_limit = 100
            except (ValueError, TypeError):
                dev_stop_limit = 100

            rt = instance_manager.rt(uid)
            w = self.workers.get(uid)
            worker_active = bool(w and w.is_running) or rt.status in ACTIVE_STATES

            if not worker_active:
                skipped.append({**self._ref(inst), "reason": "Device belum berjalan; gunakan START untuk memulai tugas"})
                continue

            if rt.status in ("STOPPING", "REBOOTING"):
                skipped.append({**self._ref(inst), "reason": f"Device sedang {rt.status}"})
                continue

            # Determine current effective mode (from running worker or last queue item)
            current_mode = (w.active_mode if w and w.is_running else rt.mode)
            tq = instance_manager.get_queue(uid)
            last_queued_mode = tq[-1]["mode"] if tq else None
            effective_last_mode = last_queued_mode or current_mode

            if effective_last_mode == mode:
                skipped.append({
                    **self._ref(inst),
                    "reason": f"Mode '{mode}' sudah sama dengan mode aktif/antrian terakhir; gunakan START/SWITCH untuk mengubah parameter stop condition"
                })
                continue

            # Validate prerequisites
            fail_reason = self._validate_target_mode_for_device(inst, mode)
            if fail_reason and mode != "redeem_only":
                # For redeem_only, re-validate at promotion time (stock may change)
                skipped.append({**self._ref(inst), "reason": f"Prasyarat tidak terpenuhi: {fail_reason}"})
                continue

            item = instance_manager.enqueue_task(uid, mode, dev_stop_mode, dev_stop_limit)
            log_event(
                LogSeverity.INFO, "BOT_CONTROL",
                f"Tugas '{mode}' ditambahkan ke antrian (posisi #{len(tq)+1}) — tugas yang berjalan tidak terganggu.",
                uid,
            )
            queued.append({**self._ref(inst), "mode": mode, "queue_id": item["queue_id"], "queue_position": len(tq) + 1})

        parts = []
        if queued:
            parts.append(f"{len(queued)} diantrekan")
        if skipped:
            parts.append(f"{len(skipped)} dilewati")
        msg = ", ".join(parts) if parts else "Tidak ada device yang diproses"
        if skipped:
            msg += " (" + "; ".join(f"{s['name']}: {s['reason']}" for s in skipped[:5]) + ")"

        return {
            "success": bool(queued) or not skipped,
            "message": msg,
            "queued": queued,
            "skipped": skipped,
        }

    def cancel_queue(self, device_uuid: str, queue_id: Optional[str] = None) -> Dict[str, Any]:
        """Cancel a specific queue item or all queued tasks for the device."""
        inst = instance_manager.get_instance(device_uuid)
        if not inst:
            return {"success": False, "message": "Instance tidak ditemukan"}

        if queue_id:
            removed = instance_manager.cancel_queue_item(device_uuid, queue_id)
            if removed:
                log_event(LogSeverity.INFO, "BOT_CONTROL", f"Item antrian ({queue_id[:8]}) dibatalkan operator.", device_uuid)
                return {"success": True, "message": f"Item antrian dihapus."}
            else:
                return {"success": False, "message": f"Item antrian tidak ditemukan (queue_id={queue_id})."}
        else:
            q_len = len(instance_manager.get_queue(device_uuid))
            instance_manager.clear_queue(device_uuid)
            log_event(LogSeverity.INFO, "BOT_CONTROL", f"Seluruh antrian ({q_len} item) dibatalkan operator.", device_uuid)
            return {"success": True, "message": f"Seluruh antrian ({q_len} item) dihapus."}

    def _promote_next_queued_task(self, device_uuid: str, stopped_reason: Optional[str] = None):
        """
        Called after a worker finishes naturally. Dequeues and starts the next queued task
        if prerequisites are still met. Skips quiz-based tasks when daily limit was hit.
        """
        tq = instance_manager.get_queue(device_uuid)
        if not tq:
            return

        # Daily Limit guard: if the task stopped due to daily limit, skip quiz-mode queued tasks
        while True:
            item = instance_manager.dequeue_next_task(device_uuid)
            if item is None:
                break

            next_mode = item["mode"]
            inst = instance_manager.get_instance(device_uuid)
            if not inst:
                break

            if stopped_reason == "DAILY_LIMIT" and next_mode in QUIZ_MODES:
                log_event(
                    LogSeverity.WARNING, "BOT_CONTROL",
                    f"Antrian mode '{next_mode}' dilewati: daily limit sudah tercapai hari ini untuk mode quiz.",
                    device_uuid,
                )
                from backend.telemetry import telemetry_manager
                telemetry_manager.emit_sync("alert_event", {
                    "device_uuid": device_uuid,
                    "type": "warning",
                    "message": f"[{inst.name}] Antrian mode '{next_mode}' dilewati karena Daily Limit sudah tercapai hari ini.",
                })
                continue  # try next in queue

            # Validate prerequisites at promotion time
            if next_mode in AI_MODES and not settings.ai_api_key:
                log_event(LogSeverity.WARNING, "BOT_CONTROL",
                          f"Antrian mode '{next_mode}' dilewati: AI API Key belum dikonfigurasi.", device_uuid)
                continue

            fail_reason = self._validate_target_mode_for_device(inst, next_mode)
            if fail_reason:
                log_event(LogSeverity.WARNING, "BOT_CONTROL",
                          f"Antrian mode '{next_mode}' dilewati: {fail_reason}.", device_uuid)
                continue

            rt = instance_manager.rt(device_uuid)
            if rt.status not in ("IDLE", "ERROR"):
                log_event(LogSeverity.WARNING, "BOT_CONTROL",
                          f"Antrian mode '{next_mode}' tidak dapat dipromosikan: status device '{rt.status}'.", device_uuid)
                # Put item back at front of queue
                with instance_manager._lock:
                    r = instance_manager.rt(device_uuid)
                    r.task_queue.insert(0, item)
                instance_manager._persist_task_queue(device_uuid, instance_manager.get_queue(device_uuid))
                break

            # Set task intent and start new worker
            instance_manager.set_task_intent(
                device_uuid, next_mode, item["stop_mode"], item["stop_limit"], completed_count=0
            )
            if rt.status == "ERROR":
                instance_manager.set_runtime(device_uuid, status="IDLE", emit=False)

            new_worker = InstanceWorker(inst)
            with self._lock:
                self.workers[device_uuid] = new_worker

            if new_worker.start(next_mode, stop_mode=item["stop_mode"], stop_limit=item["stop_limit"]):
                log_event(
                    LogSeverity.INFO, "BOT_CONTROL",
                    f"Antrian dipromosikan: mode '{next_mode}' otomatis dimulai setelah tugas sebelumnya selesai.",
                    device_uuid,
                )
            else:
                instance_manager.clear_task_intent(device_uuid, "Gagal memulai worker dari antrian")
                log_event(LogSeverity.ERROR, "BOT_CONTROL",
                          f"Gagal memulai worker untuk antrian mode '{next_mode}'.", device_uuid)
            break  # only promote one task at a time

    # ------------------------------------------------------------ restarts
    def force_restart_app(self, device_uuid: str) -> Tuple[bool, str]:
        with self._lock:
            if device_uuid in self._switching:
                self._switch_cancelled.add(device_uuid)
        with self._dev_lock(device_uuid):
            inst = instance_manager.get_instance(device_uuid)
            if not inst:
                return False, "Instance tidak ditemukan"
            rt = instance_manager.rt(device_uuid)
            if rt.status in ("OFFLINE", "REBOOTING"):
                return False, f"Device {rt.status}"
            if not self._stop_and_wait(device_uuid, "Operator clicked Force Restart App", return_to_home=False):
                return False, "Worker belum berhenti (timeout)"
            if not adb_manager.is_ready(inst.address):
                instance_manager.mark_offline(device_uuid)
                return False, "OFFLINE"
            log_event(LogSeverity.INFO, "BOT_CONTROL", "Force Restart App (Nusuk) dipicu operator", device_uuid)
            adb_manager.clear_ram(inst.address)
            try:
                adb_manager.shell(inst.address, "settings put global development_settings_enabled 0", timeout=5)
            except Exception:
                pass
            adb_manager.launch_app(inst.address)

            # Check if this instance has a pending task intent to automatically resume!
            ti = instance_manager.get_task_intent(device_uuid)
            if ti:
                self.trigger_auto_resume(device_uuid)
                return True, f"Aplikasi Nusuk di-restart — tugas '{ti.mode}' otomatis dilanjutkan"

            instance_manager.set_runtime(device_uuid, status="IDLE", mode=None, current_step="Aplikasi Nusuk di-restart")
            return True, "Aplikasi Nusuk di-restart"

    def force_restart_device(self, device_uuid: str) -> Tuple[bool, str]:
        with self._lock:
            if device_uuid in self._switching:
                self._switch_cancelled.add(device_uuid)
        with self._dev_lock(device_uuid):
            inst = instance_manager.get_instance(device_uuid)
            if not inst:
                return False, "Instance tidak ditemukan"
            rt = instance_manager.rt(device_uuid)
            if rt.status in ("OFFLINE", "REBOOTING"):
                return False, f"Device {rt.status}"
            if not self._stop_and_wait(device_uuid, "Operator clicked Force Restart Device", return_to_home=False):
                return False, "Worker belum berhenti (timeout)"
            log_event(LogSeverity.WARNING, "BOT_CONTROL", "Force Restart Device (reboot Android) dipicu operator", device_uuid)
            instance_manager.set_runtime(device_uuid, status="REBOOTING", mode=None, start_time=None,
                                         reboot_started=time.time(), reboot_saw_down=False, offline_logged=True,
                                         current_step="Sedang Restart Device - menunggu ADB kembali aktif...")
            if not adb_manager.reboot(inst.address):
                instance_manager.set_runtime(device_uuid, status="IDLE", reboot_started=None, offline_logged=False,
                                             current_step="Reboot gagal dikirim")
                return False, "Perintah reboot gagal dikirim"
            return True, "Reboot dikirim"

    def restart_many(self, device_uuids: List[str], kind: str) -> Dict[str, Any]:
        if kind not in ("app", "device"):
            raise ValueError("kind harus 'app' atau 'device'.")
        fn = self.force_restart_app if kind == "app" else self.force_restart_device
        refs = []
        for uid in dict.fromkeys(device_uuids):
            inst = instance_manager.get_instance(uid)
            refs.append((uid, inst))
        futures = [(uid, inst, self._pool.submit(fn, uid)) for uid, inst in refs if inst]
        started, skipped = [], []
        for uid, inst, f in futures:
            try:
                ok, msg = f.result(timeout=120)
            except Exception as e:
                ok, msg = False, str(e)
            (started if ok else skipped).append({**self._ref(inst), **({} if ok else {"reason": msg})})
        for uid, inst in refs:
            if not inst:
                skipped.append({"uuid": uid, "name": uid[:8], "reason": "Instance tidak ditemukan"})
        verb = "di-restart (app)" if kind == "app" else "di-reboot"
        return {"success": len(started) > 0 or not skipped, "message": self._summary(verb, started, skipped),
                "started": started, "skipped": skipped}

    # ------------------------------------------------------------ delete / lifecycle
    def delete_instance(self, device_uuid: str):
        with self._lock:
            if device_uuid in self._switching:
                self._switch_cancelled.add(device_uuid)
        with self._dev_lock(device_uuid):
            if not self._stop_and_wait(device_uuid, "Instance dihapus (soft-delete)", return_to_home=False):
                raise TimeoutError("Worker belum berhenti; coba lagi sesaat lagi.")
            instance_manager.clear_task_intent(device_uuid, reason="Instance dihapus")
            instance_manager.clear_queue(device_uuid)
            self.workers.pop(device_uuid, None)
            instance_manager.soft_delete(device_uuid)

    def startup(self):
        redroid_manager.auto_start_containers()
        n = key_service.sweep_orphan_claims()
        if n:
            log_event(LogSeverity.WARNING, "KEY_LIFECYCLE",
                      f"Startup Safe Rollback: {n} klaim 'in_progress' yatim dilepas.", None)
        instance_manager.load()
        instance_manager.start_watchdog()

    def shutdown(self):
        instance_manager.stop_watchdog()
        with self._lock:
            for uid in self._switching:
                self._switch_cancelled.add(uid)
        for w in list(self.workers.values()):
            if w.is_running:
                w.stop("Server shutdown")
        for w in list(self.workers.values()):
            w.join(30)
            w._safe_rollback_key()
        key_service.sweep_orphan_claims()


orchestrator = Orchestrator()
