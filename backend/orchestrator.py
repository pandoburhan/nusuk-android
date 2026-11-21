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
from backend import key_service

logger = logging.getLogger(__name__)

MODES = ("answer_only", "redeem_only", "auto")
AI_MODES = ("answer_only", "auto")
ACTIVE_STATES = ("RUNNING", "RECOVERING")


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

                if rt.status == "ERROR":
                    instance_manager.set_runtime(uid, status="IDLE", emit=False)
                worker = InstanceWorker(inst)
                with self._lock:
                    self.workers[uid] = worker
                if worker.start(mode, stop_mode=dev_stop_mode, stop_limit=dev_stop_limit):
                    started.append(self._ref(inst))
                else:
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
            adb_manager.launch_app(inst.address)
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
            self.workers.pop(device_uuid, None)
            instance_manager.soft_delete(device_uuid)

    def startup(self):
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
