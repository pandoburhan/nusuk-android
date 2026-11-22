import os
import re
import time
import random
import threading
import datetime
import json
import logging
import traceback
from typing import Optional, Dict, Any, List

import uiautomator2 as u2
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import SessionLocal
from backend.models import (
    QuizLog, QuizResultStatus, LogSeverity, ClaimStatus, Instance,
)
from backend.adb_manager import adb_manager, is_transport_or_socket_error
from backend.quiz_solver import quiz_solver, AISolverError
from backend.screen_classifier import (
    screen_classifier, ScreenState, bounds_center, is_bounds_in_viewport, scale_ref_xy,
)
from backend.telemetry import telemetry_manager
from backend.event_log import log_event
from backend.instance_manager import instance_manager, LoginStatus
from backend.time_service import utc_now_naive
from backend.forensic_service import forensic_service
from backend import key_service

logger = logging.getLogger(__name__)

LOGIN_FLOW_STATES = {
    ScreenState.SCREEN_WELCOME_SLIDES,
    ScreenState.SCREEN_LOGIN_MAIN,
    ScreenState.SCREEN_LOGIN_METHOD_POPUP,
    ScreenState.SCREEN_CREDENTIALS_FORM,
    ScreenState.SCREEN_VERIFY_OTP_FORM,
    ScreenState.SCREEN_LOGGED_OUT_TIMED_OUT,
}

AUTHENTICATED_STATES = {
    ScreenState.SCREEN_NUSUK_HOME,
    ScreenState.SCREEN_CHOOSE_DOOR,
    ScreenState.SCREEN_DOOR_CONFIRM_POPUP,
    ScreenState.SCREEN_QUIZ_QUESTION,
    ScreenState.SCREEN_QUIZ_RESULT,
    ScreenState.SCREEN_REDEEM_KEYS,
    ScreenState.SCREEN_REDEEM_SUCCESS,
}


class DeviceOffline(Exception):
    """Raised inside a worker when the exact ADB target disappeared (not an app error)."""


class InstanceWorker:
    """
    One fully isolated worker per device (blueprint 3.1B): own thread, stop event, u2 handle bound to this
    device's ip:port, claim lock, recovery counter. It NEVER touches another device and uses a fresh DB
    session for every DB operation (thread-safe).
    """

    def __init__(self, inst: Instance):
        self.uuid = inst.uuid
        self.name = inst.name
        self.connection_type = (getattr(inst, "connection_type", None) or "tcp").lower()
        self.device_id = inst.address  # ip:port or USB serial (connection attribute only)
        self.d: Optional[u2.Device] = None
        self.active_mode: Optional[str] = None
        self.is_running = False
        self._stop_requested = False
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._return_to_home_on_stop = True
        self._for_switch = False
        self.final_status: Optional[str] = None  # IDLE (default) | ERROR | OFFLINE
        self._stopped_reason: Optional[str] = None  # e.g. "DAILY_LIMIT", "LIMIT_REACHED", "EXHAUSTED"

        self.current_screen = ScreenState.SCREEN_UNKNOWN_OR_LOADING
        self.start_time: Optional[float] = None
        self.consecutive_recoveries = 0
        self.consecutive_unknown_count = 0
        self.stop_mode: str = "limit"
        self.stop_limit: int = 100
        self.session_completed_count: int = 0
        self.current_claim_id: Optional[int] = None
        self.current_key_id: Optional[int] = None
        self.current_key_code: Optional[str] = None
        self.key_timeout_counts: Dict[int, int] = {}
        self.last_chosen_door_name = "Category"
        self.last_quiz_context: Optional[Dict[str, Any]] = None
        self.question_start_time: Optional[float] = None
        self.redeem_start_time: Optional[float] = None
        self.unknown_first_seen_time: Optional[float] = None
        self.last_inspect_info: Optional[Dict[str, Any]] = None
        self._prefetched_info: Optional[Dict[str, Any]] = None
        self._screen_w: int = 720
        self._screen_h: int = 1280
        self._cycle_start_time: Optional[float] = None
        self._cycle_timings: Dict[str, int] = {}
        self._last_online_check_at: float = 0.0
        self._custom_stop_step: Optional[str] = None
        self._login_alert_sent_state: Optional[str] = None

    # ------------------------------------------------------------------ properties
    @property
    def current_app_keys(self) -> int:
        v = instance_manager.rt(self.uuid).current_app_keys
        return v if v is not None else 0

    def _sleep(self, seconds: float) -> bool:
        """Interruptible sleep. False if interrupted by a stop request."""
        if self._stop_requested:
            return False
        if seconds <= 0:
            return True
        return not self._stop_event.wait(timeout=seconds)

    def _in_viewport(self, bounds: Optional[tuple], margin_bottom: Optional[int] = None) -> bool:
        return is_bounds_in_viewport(bounds, self._screen_w, self._screen_h, margin_bottom=margin_bottom)

    def _scale_xy(self, ref_x: int, ref_y: int) -> tuple:
        """Proportionally scale a 720x1280 fallback coordinate to this device's actual screen size."""
        return scale_ref_xy(ref_x, ref_y, self._screen_w, self._screen_h)

    def _swipe_rel(self, fx_pct: float, fy_pct: float, tx_pct: float, ty_pct: float, duration: float = 0.20):
        """Proportionally swipe using relative screen coordinates (0.0..1.0) so physical phones work identically."""
        fx = max(1, int(round(self._screen_w * fx_pct)))
        fy = max(1, int(round(self._screen_h * fy_pct)))
        tx = max(1, int(round(self._screen_w * tx_pct)))
        ty = max(1, int(round(self._screen_h * ty_pct)))
        self.d.swipe(fx, fy, tx, ty, duration)

    def _click_bounds(self, bounds: Optional[tuple], fallback_xy: Optional[tuple] = None) -> bool:
        """Click the center of pre-parsed XML bounds (1 JSON-RPC call instead of 4-5 UiObject RPCs).
        When `fallback_xy` is supplied in 720x1280 reference coordinates, it is auto-scaled."""
        if not self.d:
            return False
        center = bounds_center(bounds)
        if center:
            self.d.click(center[0], center[1])
            return True
        if fallback_xy:
            sx, sy = self._scale_xy(fallback_xy[0], fallback_xy[1])
            self.d.click(sx, sy)
            return True
        return False

    def _press_hide_keyboard(self):
        """Dismiss soft keyboard via persistent u2 JSON-RPC (no adb.exe subprocess spawn)."""
        try:
            if self.d:
                self.d.press(111)
                return
        except Exception as e:
            logger.debug(f"u2 press(111) failed on {self.device_id}, falling back to adb: {e}")
        adb_manager.hide_keyboard(self.device_id)

    def log_system(self, severity: LogSeverity, category: str, message: str, last_screen: Optional[str] = None):
        log_event(severity, category, message, device_uuid=self.uuid,
                  last_screen=last_screen or str(getattr(self.current_screen, "value", self.current_screen)))

    def update_telemetry_status(self, step: str, bot_status: Optional[str] = None):
        status = bot_status or ("RUNNING" if self.is_running else "IDLE")
        instance_manager.set_runtime(
            self.uuid, status=status, mode=self.active_mode, current_step=step,
            current_screen=str(getattr(self.current_screen, "value", self.current_screen)),
            start_time=self.start_time if self.is_running else None,
            consecutive_recoveries=self.consecutive_recoveries,
            stop_mode=self.stop_mode,
            stop_limit=self.stop_limit,
            session_completed_count=self.session_completed_count,
        )

    def _set_login_state(
        self,
        login_status: str,
        step: Optional[str] = None,
        error: Optional[str] = None,
        clear_error: bool = False,
    ):
        kw: Dict[str, Any] = {
            "login_status": login_status,
            "current_screen": str(getattr(self.current_screen, "value", self.current_screen)),
        }
        if step is not None:
            kw["current_step"] = step
        if clear_error:
            kw["login_error"] = None
        elif error is not None:
            kw["login_error"] = error
        instance_manager.set_runtime(self.uuid, **kw)

    def _alert(self, type_: str, message: str):
        telemetry_manager.emit_sync("alert_event", {"device_uuid": self.uuid, "type": type_, "message": f"[{self.name}] {message}"})

    def _preview_enabled(self) -> bool:
        inst = instance_manager.get_instance(self.uuid)
        return bool(inst and inst.live_preview_enabled)

    # ------------------------------------------------------------------ device connection
    def connect_device(self, retries: int = 2) -> bool:
        try:
            os.environ["ADBUTILS_ADB_PATH"] = adb_manager.adb_path
            if not adb_manager.ensure_connected(self.device_id, retries=retries, connection_type=self.connection_type):
                return False
            self.d = u2.connect(self.device_id)
            # Tune uiautomator2 internal timings: avoid hidden delays and 20s implicit hangs
            try:
                self.d.settings["wait_timeout"] = 3.0
                self.d.settings["operation_delay"] = (0, 0)
            except Exception as cfg_err:
                logger.debug(f"Could not apply u2 settings on {self.device_id}: {cfg_err}")
            # Detect dynamic screen resolution (supports 1080x2400, 1080x2340, 720x1600, 720x1280, etc.)
            w, h = 0, 0
            try:
                w, h = self.d.window_size()
            except Exception:
                pass
            if not (w and h):
                d_info = self.d.info or {}
                w = int(d_info.get("displayWidth") or 0)
                h = int(d_info.get("displayHeight") or 0)
            if w > 0 and h > 0:
                self._screen_w, self._screen_h = int(w), int(h)
                logger.info(f"Worker [{self.name}] connected ({self.connection_type.upper()}: {self.device_id}) screen={self._screen_w}x{self._screen_h}")
            return True
        except Exception as e:
            logger.debug(f"uiautomator2 connect failed for {self.device_id}: {e}")
            self.d = None
            return False

    def _check_still_online(self, force: bool = True) -> bool:
        """Called when screen inspection is inconclusive: distinguish OFFLINE from a stuck app.
        Performs self-healing ADB reconnect (TCP/Wireless) or USB re-check before raising DeviceOffline."""
        now = time.monotonic()
        if not force and self.d is not None and (now - self._last_online_check_at) < 2.0:
            return True
        self._last_online_check_at = now

        if adb_manager.is_ready(self.device_id):
            return True

        is_usb = (self.connection_type == "usb") or (not adb_manager.is_tcp_serial(self.device_id))
        logger.warning(f"Worker [{self.name}] detected potential ADB disconnect on {self.device_id} ({self.connection_type}). Checking reconnect...")
        for attempt in range(1, 4):
            if self._stop_requested:
                return False
            if not self._sleep(1.5):
                return False
            ready = (
                adb_manager.is_ready(self.device_id)
                if is_usb
                else adb_manager.ensure_connected(self.device_id, retries=1, connection_type=self.connection_type)
            )
            if ready:
                logger.info(f"Worker [{self.name}] ADB link restored on attempt #{attempt}. Refreshing device handle...")
                if self.connect_device(retries=1):
                    return True

        logger.error(f"Worker [{self.name}] device {self.device_id} remains unreachable after 3 reconnect attempts.")
        raise DeviceOffline()

    # ------------------------------------------------------------------ lifecycle
    def start(self, mode: str, stop_mode: str = "limit", stop_limit: int = 100, initial_completed: int = 0) -> bool:
        with self._lock:
            if self.is_running:
                return False
            self.active_mode = mode
            self.stop_mode = stop_mode or "limit"
            try:
                self.stop_limit = int(stop_limit)
                if self.stop_limit < 1:
                    self.stop_limit = 100
            except (ValueError, TypeError):
                self.stop_limit = 100
            self.session_completed_count = max(0, int(initial_completed or 0))

            self._stop_requested = False
            self._stop_event.clear()
            self._return_to_home_on_stop = True
            self._for_switch = False
            self.is_running = True
            self.final_status = None
            self._stopped_reason = None
            self.start_time = time.time()
            self.consecutive_recoveries = 0
            self.consecutive_unknown_count = 0
            self.current_claim_id = None
            self._prefetched_info = None
            self._cycle_start_time = None
            self._cycle_timings = {}
            self._custom_stop_step = None
            self._login_alert_sent_state = None
            instance_manager.clear_login_inputs(self.uuid)
            instance_manager.set_runtime(
                self.uuid,
                emit=False,
                login_error=None,
                stop_mode=self.stop_mode,
                stop_limit=self.stop_limit,
                session_completed_count=self.session_completed_count,
            )
            progres_str = f" [Resume progres: {self.session_completed_count}/{self.stop_limit}]" if self.session_completed_count > 0 else ""
            self.log_system(LogSeverity.INFO, "BOT_CONTROL",
                            f"Starting bot in mode '{mode}' on target {self.device_id} (Stop condition: {self.stop_mode}, target={self.stop_limit}){progres_str}")
            self.update_telemetry_status(f"Starting bot in mode '{mode}'{progres_str}...", bot_status="RUNNING")
            self._worker_thread = threading.Thread(target=self._run_loop, name=f"worker-{self.uuid[:8]}", daemon=True)
            self._worker_thread.start()
            return True

    def update_stop_condition(self, stop_mode: str, stop_limit: int):
        with self._lock:
            self.stop_mode = stop_mode or "limit"
            try:
                self.stop_limit = int(stop_limit)
                if self.stop_limit < 1:
                    self.stop_limit = 100
            except (ValueError, TypeError):
                self.stop_limit = 100
            instance_manager.set_runtime(
                self.uuid,
                stop_mode=self.stop_mode,
                stop_limit=self.stop_limit,
            )

    def stop(
        self,
        reason: str = "Operator requested stop",
        return_to_home: bool = True,
        for_switch: bool = False,
    ):
        with self._lock:
            if not self.is_running:
                return
            self.question_start_time = None
            self.redeem_start_time = None
            self.unknown_first_seen_time = None
            self.last_quiz_context = None
            self._prefetched_info = None
            self._cycle_start_time = None
            self._cycle_timings = {}
            self._custom_stop_step = None
            self._return_to_home_on_stop = return_to_home
            self._for_switch = for_switch
            self._stop_requested = True
            self._stop_event.set()
            instance_manager.clear_login_inputs(self.uuid)
            if not for_switch:
                self.log_system(LogSeverity.INFO, "BOT_CONTROL", f"Stopping bot: {reason}")
                self.update_telemetry_status(f"Stopping bot: {reason}...", bot_status="STOPPING")

    def join(self, timeout: float = 60.0) -> bool:
        t = self._worker_thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout)
            return not t.is_alive()
        return True

    def _safe_rollback_key(self):
        """Release the in_progress claim of THIS device so the key is eligible again."""
        if self.current_claim_id:
            cid, code = self.current_claim_id, self.current_key_code
            self.current_claim_id = None
            try:
                rolled = key_service.rollback_claim(cid)
                if rolled or code:
                    self.log_system(LogSeverity.WARNING, "KEY_LIFECYCLE",
                                    f"Safe Rollback: klaim key '{rolled or code}' dilepas, kembali tersedia untuk device ini")
            except Exception as e:
                logger.error(f"Error rolling back claim: {e}")

    def _spoof_adb_status(self):
        """Hide Developer Mode flag in Settings.Global before launching Nusuk without killing adbd."""
        if not self.d:
            return
        try:
            self.log_system(
                LogSeverity.INFO,
                "ANTI_DETECTION",
                "Menyembunyikan status Developer Mode (tanpa mematikan ADB)",
            )
            self.d.shell("settings put global development_settings_enabled 0")
        except Exception as e:
            self.log_system(
                LogSeverity.WARNING,
                "ANTI_DETECTION",
                f"Gagal menyembunyikan status Developer Mode (tanpa mematikan ADB): {e}",
            )

    def _return_to_homepage(self):
        """Return Nusuk to the home screen (restart app, dismiss location popup)."""
        try:
            self.update_telemetry_status("Returning app to Homepage...", bot_status="STOPPING")
            adb_manager.clear_ram(self.device_id)
            self._spoof_adb_status()
            adb_manager.launch_app(self.device_id)
            start_t = time.time()
            while time.time() - start_t < 15.0:
                time.sleep(0.6)
                info = self._inspect_screen(max_retries=0)
                if info["state"] == ScreenState.SCREEN_LOCATION_POPUP:
                    loc_data = info.get("location_popup_data") or {}
                    self._click_bounds(loc_data.get("later_bounds"), fallback_xy=(360, 1137))
                    time.sleep(0.4)
                    continue
                if info["state"] in [ScreenState.SCREEN_NUSUK_HOME, ScreenState.SCREEN_CHOOSE_DOOR]:
                    break
        except Exception as e:
            logger.debug(f"Error while returning to Homepage on {self.device_id}: {e}")

    def _dump_xml_fast(self) -> str:
        """
        Single-shot hierarchy dump via u2 JSON-RPC, bypassing u2's built-in
        @retry(HierarchyEmptyError, tries=3, delay=1) which injects 1-2s hidden sleeps
        during Jetpack Compose frame transitions.
        Connection/RPC exceptions propagate to `_inspect_screen` so OFFLINE/recovery stay intact.
        """
        if not self.d:
            return ""
        max_depth = 50
        try:
            max_depth = int(self.d.settings.get("max_depth") or 50)
        except Exception:
            max_depth = 50
        content = self.d.jsonrpc.dumpWindowHierarchy(False, max_depth)
        if not content or '<hierarchy rotation="0" />' in content:
            return ""
        return content

    def _sync_app_keys(self, new_keys: Optional[int]):
        if new_keys is None:
            return
        prev = instance_manager.rt(self.uuid).current_app_keys
        if prev != new_keys:
            instance_manager.set_runtime(self.uuid, emit=False, current_app_keys=new_keys)
            telemetry_manager.emit_sync("app_keys_update", {"device_uuid": self.uuid, "current_app_keys": new_keys})
            instance_manager.persist_app_keys(self.uuid, new_keys)

    def _inspect_screen(
        self,
        max_retries: int = 2,
        retry_delay: float = 0.12,
        verify_package: bool = False,
    ) -> Dict[str, Any]:
        if not self.d:
            if not self.connect_device():
                return {"state": ScreenState.SCREEN_UNKNOWN_OR_LOADING}

        for attempt in range(max_retries + 1):
            try:
                xml = self._dump_xml_fast()
                if xml:
                    self._last_raw_xml = xml
                if not xml or "<hierarchy" not in xml:
                    if attempt < max_retries:
                        if not self._sleep(retry_delay):
                            return {"state": ScreenState.SCREEN_UNKNOWN_OR_LOADING}
                        continue
                    # Empty hierarchy even after micro-retries: fallback to app_current()
                    # to distinguish closed/crashed app from Compose loading frame.
                    curr_app = self.d.app_current()
                    pkg = curr_app.get("package", "")
                    if pkg and "com.moh.nusukapp" not in pkg:
                        info = {"state": ScreenState.SCREEN_LAUNCHER_OR_CLOSED, "current_app_keys": None, "all_texts": []}
                        self.current_screen = info["state"]
                        self.last_inspect_info = info
                        return info
                    return {"state": ScreenState.SCREEN_UNKNOWN_OR_LOADING}

                info = screen_classifier.parse_hierarchy(
                    xml, current_package=None, screen_w=self._screen_w, screen_h=self._screen_h
                )

                # Prioritize system dialog/ANR: do not overwrite to LAUNCHER_OR_CLOSED
                if info["state"] == ScreenState.SCREEN_SYSTEM_DIALOG:
                    self.current_screen = info["state"]
                    self.last_inspect_info = info
                    return info

                # Guard #1: Only bypass `app_current()` (dumpsys) when XML clearly belongs
                # to com.moh.nusukapp, has no Android crash dialog, and resolved to a known screen.
                need_pkg_check = (
                    verify_package
                    or not info.get("has_nusuk_package")
                    or info["state"] == ScreenState.SCREEN_UNKNOWN_OR_LOADING
                )
                if need_pkg_check:
                    curr_app = self.d.app_current()
                    pkg = curr_app.get("package", "")
                    if pkg and "com.moh.nusukapp" not in pkg:
                        info["state"] = ScreenState.SCREEN_LAUNCHER_OR_CLOSED
                        self.current_screen = info["state"]
                        self.last_inspect_info = info
                        return info
                    if info["state"] == ScreenState.SCREEN_UNKNOWN_OR_LOADING and "com.moh.nusukapp" in pkg:
                        if attempt < max_retries:
                            if not self._sleep(retry_delay):
                                return info
                            continue

                self.current_screen = info["state"]
                self.last_inspect_info = info
                self._sync_app_keys(info.get("current_app_keys"))
                return info
            except Exception as e:
                logger.debug(f"Inspection attempt {attempt + 1} error on {self.device_id}: {e}")
                if is_transport_or_socket_error(e):
                    raise
                if attempt < max_retries:
                    if not self._sleep(retry_delay):
                        return {"state": ScreenState.SCREEN_UNKNOWN_OR_LOADING}
                    continue
                self.d = None  # force reconnect / offline check next time
                return {"state": ScreenState.SCREEN_UNKNOWN_OR_LOADING}
        return {"state": ScreenState.SCREEN_UNKNOWN_OR_LOADING}

    def _wait_for_states(
        self,
        target_states: set,
        timeout: float = 5.0,
        poll_interval: float = 0.12,
        initial_delay: float = 0.08,
        accept_fn=None,
    ) -> Optional[Dict[str, Any]]:
        """
        Adaptive Smart Wait: polls UI XML at short intervals until any state in `target_states`
        (and optional `accept_fn(info)`) is detected, returning immediately with the parsed info
        so the next FSM iteration can consume it without dumping XML twice.
        """
        if initial_delay > 0 and not self._sleep(initial_delay):
            return None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._stop_requested:
                return None
            info = self._inspect_screen(max_retries=0)
            st = info["state"]
            if st in (
                ScreenState.SCREEN_LAUNCHER_OR_CLOSED,
                ScreenState.SCREEN_DAILY_LIMIT_POPUP,
                ScreenState.SCREEN_LOGGED_OUT_TIMED_OUT,
            ):
                return info
            if st in target_states and (accept_fn is None or accept_fn(info)):
                return info
            if self.d is None:
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if not self._sleep(min(poll_interval, remaining)):
                return None
        return None

    def _execute_auto_recovery(self, trigger_reason: str):
        self.consecutive_recoveries += 1
        self.question_start_time = None
        self.redeem_start_time = None
        self.unknown_first_seen_time = None
        self.last_quiz_context = None
        self._prefetched_info = None
        self._cycle_start_time = None
        self._cycle_timings = {}

        ui_snippet, screenshot_name = "", ""
        try:
            info = self.last_inspect_info or {}
            all_texts = info.get("all_texts", [])
            ignore_keywords = [
                "android system", "wifi", "battery", "usb debugging", "notification", "signal",
                "alarm set", "location request", "system notification"
            ]
            filtered_texts = []
            for t in all_texts:
                t_clean = t.strip()
                if not t_clean:
                    continue
                if re.match(r"^\d{1,2}:\d{2}(\s*(am|pm))?$", t_clean, re.IGNORECASE):
                    continue
                if any(kw in t_clean.lower() for kw in ignore_keywords):
                    continue
                filtered_texts.append(t_clean)

            display_texts = filtered_texts if filtered_texts else all_texts
            if display_texts:
                ui_snippet = " | ".join(display_texts[:14])
            # Diagnostic screenshot only when Live Preview is ON (resource-saving rule 3.3)
            if self.d and self._preview_enabled():
                debug_dir = os.path.join(os.path.dirname(__file__), "..", "logs", "debug_screenshots")
                os.makedirs(debug_dir, exist_ok=True)
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                ss_path = os.path.join(debug_dir, f"recovery_{self.uuid[:8]}_{ts}.png")
                self.d.screenshot(ss_path)
                if os.path.exists(ss_path):
                    screenshot_name = os.path.basename(ss_path)
        except Exception as e:
            logger.debug(f"Diagnostic capture exception: {e}")

        diag_msg = f"Triggering Auto-Recovery #{self.consecutive_recoveries}: {trigger_reason}"
        if ui_snippet:
            diag_msg += f" | UI Snippet: [{ui_snippet}]"
        if screenshot_name:
            diag_msg += f" | Screenshot: {screenshot_name}"
        self.log_system(LogSeverity.WARNING, "APP_RECOVERY", diag_msg)
        self.update_telemetry_status(f"Recovering: {trigger_reason} (Attempt #{self.consecutive_recoveries})...", bot_status="RECOVERING")
        self._alert("warning", f"Auto-Recovery triggered: {trigger_reason}")

        self._safe_rollback_key()

        if self.consecutive_recoveries >= settings.circuit_breaker_limit:
            msg = f"Circuit Breaker tripped: {self.consecutive_recoveries} consecutive recoveries exceeded limit. Halting bot."
            self.log_system(LogSeverity.CRITICAL, "CIRCUIT_BREAKER", msg)
            self._alert("error", msg)
            self.final_status = "ERROR"
            self._stop_requested = True
            self._stop_event.set()
            return

        adb_manager.clear_ram(self.device_id)
        if self._stop_requested:
            return
        self._spoof_adb_status()
        adb_manager.launch_app(self.device_id)

        start_wait = time.time()
        while time.time() - start_wait < settings.loading_timeout_seconds:
            if self._stop_requested:
                return
            if not self._sleep(0.5):
                return
            info = self._inspect_screen(max_retries=0)
            if info["state"] == ScreenState.SCREEN_LOCATION_POPUP:
                self._handle_screen_location_popup(info)
                continue
            if info["state"] in [ScreenState.SCREEN_NUSUK_HOME, ScreenState.SCREEN_CHOOSE_DOOR] or info["state"] in LOGIN_FLOW_STATES:
                self.log_system(LogSeverity.INFO, "APP_RECOVERY", f"Successfully recovered to state: {info['state']}")
                self.consecutive_unknown_count = 0
                self._prefetched_info = info
                return
            if info["state"] == ScreenState.SCREEN_UNKNOWN_OR_LOADING:
                self._check_still_online(force=False)

        self.log_system(LogSeverity.ERROR, "APP_RECOVERY", "Recovery cold boot timed out waiting for Home screen")

    # ------------------------------------------------------------------ claims (many-to-many)
    def _lock_key(self) -> Optional[Dict[str, Any]]:
        res = key_service.lock_next_key(self.uuid)
        if not res:
            return None
        claim_id, key_id, code = res
        self.current_claim_id, self.current_key_id, self.current_key_code = claim_id, key_id, code
        self.log_system(LogSeverity.INFO, "KEY_LIFECYCLE", f"Locked key '{code}' (ID: {key_id}) for redemption")
        telemetry_manager.emit_sync("key_event", {"device_uuid": self.uuid, "action": "locked", "code": code, "id": key_id})
        return {"claim_id": claim_id, "id": key_id, "code": code}

    def _finish_claim(
        self,
        status: ClaimStatus,
        note: Optional[str] = None,
        duration_ms: Optional[int] = None,
        timing_detail: Optional[str] = None,
    ):
        cid, key_id, code = self.current_claim_id, self.current_key_id, self.current_key_code
        self.current_claim_id = None
        if not cid:
            return
        try:
            info = key_service.complete_claim(cid, status, note, duration_ms)
        except Exception as e:
            logger.error(f"Error completing claim: {e}")
            return
        dur_text = f" | Duration: {round(duration_ms / 1000.0, 2)}s" if duration_ms else ""
        detail_text = f" | {timing_detail}" if timing_detail else ""
        self.log_system(LogSeverity.INFO, "KEY_LIFECYCLE",
                        f"Key '{code}' status changed to '{status.value}' (Note: {note or 'None'}{dur_text}{detail_text})")
        if info and info.get("blacklisted_now"):
            self.log_system(LogSeverity.WARNING, "KEY_LIFECYCLE", f"Key '{code}' di-BLACKLIST global (ditolak Nusuk): {note}")
        telemetry_manager.emit_sync("key_event", {
            "device_uuid": self.uuid, "action": status.value, "code": code, "id": key_id,
            "note": note, "duration_ms": duration_ms,
        })

        if status == ClaimStatus.USED and self.active_mode == "redeem_only":
            self.session_completed_count += 1
            instance_manager.set_runtime(self.uuid, session_completed_count=self.session_completed_count)
            instance_manager.update_task_progress(self.uuid, self.session_completed_count)
            if self.stop_mode == "limit" and self.session_completed_count >= self.stop_limit:
                msg = f"Target stop condition tercapai: {self.session_completed_count}/{self.stop_limit} key sukses dimasukkan. Bot berhenti elegan."
                self.log_system(LogSeverity.INFO, "BOT_CONTROL", msg)
                self.update_telemetry_status(msg, bot_status="IDLE")
                self._alert("info", msg)
                instance_manager.clear_task_intent(self.uuid, "Target limit reached")
                self._stopped_reason = "LIMIT_REACHED"
                self._stop_requested = True

    def _record_quiz_log(self, door: str, question: str, options: List[str], chosen_ans: str, chosen_idx: int,
                         is_correct: bool, duration_ms: int, ai_duration_ms: Optional[int],
                         timing_detail: Optional[str] = None):
        db: Session = SessionLocal()
        try:
            entry = QuizLog(
                device_id=self.device_id, device_uuid=self.uuid, mode=self.active_mode or "unknown",
                door_category=door, question_text=question,
                options_json=json.dumps(options, ensure_ascii=False),
                ai_selected_answer=chosen_ans, ai_selected_index=chosen_idx,
                result_status=QuizResultStatus.CORRECT if is_correct else QuizResultStatus.INCORRECT,
                duration_ms=duration_ms, ai_duration_ms=ai_duration_ms,
                created_at=utc_now_naive(),
            )
            db.add(entry)
            db.commit()
            db.refresh(entry)
            telemetry_manager.emit_sync("quiz_event", {
                "device_uuid": self.uuid, "id": entry.id, "door": door, "question": question,
                "chosen_answer": chosen_ans, "is_correct": is_correct,
                "duration_ms": duration_ms, "ai_duration_ms": ai_duration_ms,
            })
        except Exception as e:
            logger.error(f"Error saving quiz log: {e}")
            db.rollback()
        finally:
            db.close()
        dur_s = f"{duration_ms / 1000.0:.2f}s" if duration_ms else "-"
        ai_s = f"{ai_duration_ms / 1000.0:.2f}s" if ai_duration_ms is not None else "-"
        extra = f" | {timing_detail}" if timing_detail else ""
        self.log_system(
            LogSeverity.INFO,
            "QUIZ_ANSWER",
            f"Quiz recorded: {door} | Answer: '{chosen_ans}' | Result: {'CORRECT' if is_correct else 'INCORRECT'} "
            f"| Dur: {dur_s} (AI: {ai_s}){extra}",
        )

    def _handle_screen_daily_limit_popup(self, info: Dict[str, Any]):
        """
        Handle Nusuk 'Something Went Wrong / You've reached your daily limit. Please try again tomorrow.'
        In answer_only / auto: halt THIS instance completely to IDLE without recording a QuizLog,
        without advancing stop_limit, and without triggering Auto-Recovery or Circuit Breaker.
        """
        dl_data = info.get("daily_limit_data") or {}
        popup_msg = dl_data.get("message") or "You've reached your daily limit. Please try again tomorrow."
        close_bounds = dl_data.get("close_bounds")

        # Attempt safe dismissal of the dialog without hanging if close button is absent
        try:
            if close_bounds and self.d:
                self._click_bounds(close_bounds)
                self._sleep(0.2)
            elif self.d:
                self.d.press("back")
                self._sleep(0.2)
        except Exception as e:
            logger.debug(f"Safe dismiss of daily limit popup failed on {self.device_id}: {e}")

        if self.active_mode in ("answer_only", "auto"):
            # Discard pending quiz context so neither QuizLog nor stop_limit is polluted
            self.last_quiz_context = None
            self.question_start_time = None
            self._cycle_start_time = None
            self._cycle_timings = {}
            self._prefetched_info = None
            self._safe_rollback_key()

            step_msg = "Daily Limit Reached — Coba lagi besok (reset 00:00 Arab Saudi)"
            self._custom_stop_step = step_msg
            instance_manager.clear_task_intent(self.uuid, "Daily limit reached")
            self.log_system(
                LogSeverity.WARNING,
                "DAILY_LIMIT",
                f"Daily quiz limit reached ('{popup_msg}') — Bot dihentikan total.",
                last_screen=ScreenState.SCREEN_DAILY_LIMIT_POPUP,
            )
            self._alert("warning", f"Daily Limit Reached ({popup_msg}) — Bot dihentikan total. Coba lagi besok (reset 00:00 Arab Saudi).")
            self.update_telemetry_status(step_msg, bot_status="STOPPING")
            self.final_status = "IDLE"
            self._stopped_reason = "DAILY_LIMIT"
            self._return_to_home_on_stop = True
            self._stop_requested = True
            self._stop_event.set()
            return

        # In redeem_only mode (e.g., leftover dialog), just dismiss and continue
        self.update_telemetry_status("Menutup dialog daily limit untuk melanjutkan redeem...")

    # ------------------------------------------------------------------ SCREEN HANDLERS
    def _handle_screen_location_popup(self, info: Dict[str, Any]):
        self.update_telemetry_status("Dismissing 'Enable Location' popup by clicking 'Later'...")
        loc_data = info.get("location_popup_data") or {}
        self._click_bounds(loc_data.get("later_bounds"), fallback_xy=(360, 1137))
        nxt = self._wait_for_states(
            {ScreenState.SCREEN_NUSUK_HOME, ScreenState.SCREEN_CHOOSE_DOOR},
            timeout=3.5,
            poll_interval=0.12,
            initial_delay=0.10,
        )
        if nxt:
            self._prefetched_info = nxt

    def _handle_screen_nusuk_home(self, info: Dict[str, Any]):
        self.update_telemetry_status("Navigating to Challenge from Home...")
        home_data = info.get("home_data") or {}
        start_bounds = home_data.get("start_challenge_bounds")

        if not self._in_viewport(start_bounds):
            # Controlled proportional swipe up (non-fling) to reveal "Start the challenge" below fold
            self._swipe_rel(0.50, 0.70, 0.50, 0.35, 0.22)
            swiped = self._wait_for_states(
                {ScreenState.SCREEN_NUSUK_HOME, ScreenState.SCREEN_CHOOSE_DOOR},
                timeout=1.5,
                poll_interval=0.12,
                initial_delay=0.12,
                accept_fn=lambda inf: (
                    inf["state"] == ScreenState.SCREEN_CHOOSE_DOOR
                    or self._in_viewport((inf.get("home_data") or {}).get("start_challenge_bounds"))
                ),
            )
            if swiped:
                if swiped["state"] == ScreenState.SCREEN_CHOOSE_DOOR:
                    self._prefetched_info = swiped
                    return
                start_bounds = (swiped.get("home_data") or {}).get("start_challenge_bounds")

        if self._stop_requested:
            return

        if not self._in_viewport(start_bounds):
            # Avoid blind-clicking unrelated Home cards (e.g. "Download the Quran" / "Nusuk Market")
            logger.debug(f"Worker [{self.name}] 'Start the challenge' button still not visible on Home; retrying scroll")
            self._sleep(0.25)
            return

        self._click_bounds(start_bounds)
        nxt = self._wait_for_states(
            {ScreenState.SCREEN_CHOOSE_DOOR, ScreenState.SCREEN_LOCATION_POPUP},
            timeout=6.0,
            poll_interval=0.12,
            initial_delay=0.12,
        )
        if nxt:
            self._prefetched_info = nxt

    def _handle_screen_choose_door(self, info: Dict[str, Any]):
        keys = self.current_app_keys
        self.update_telemetry_status(f"On Door selection. Keys on account: {keys}")
        choose_data = info.get("choose_door_data") or {}
        keys_tab_bounds = choose_data.get("keys_tab_bounds")

        if self.active_mode == "redeem_only":
            self.update_telemetry_status("Mode is redeem_only -> Switching to Keys tab...")
            self.redeem_start_time = time.time()
            self._click_bounds(keys_tab_bounds, fallback_xy=(360, 1218))
            nxt = self._wait_for_states(
                {ScreenState.SCREEN_REDEEM_KEYS},
                timeout=5.0,
                poll_interval=0.12,
                initial_delay=0.10,
            )
            if nxt:
                self._prefetched_info = nxt
            return

        # If answer_only or auto:
        if keys == 0:
            if self.active_mode == "answer_only":
                msg = "Selesai: Seluruh kunci/nyawa pada akun telah habis (0)."
                self.log_system(LogSeverity.INFO, "BOT_CONTROL", msg)
                self.update_telemetry_status(msg, bot_status="IDLE")
                self._alert("info", msg)
                self._stop_requested = True
                return
            elif self.active_mode == "auto":
                if key_service.eligible_count(self.uuid) == 0:
                    msg = "Selesai: Nyawa akun 0 dan stok key eligible untuk device ini habis."
                    self.log_system(LogSeverity.INFO, "BOT_CONTROL", msg)
                    self.update_telemetry_status(msg, bot_status="IDLE")
                    self._alert("info", msg)
                    self._stop_requested = True
                    return
                self.update_telemetry_status("Account keys = 0 in Auto mode -> Moving to redeem key...")
                self.redeem_start_time = time.time()
                self._click_bounds(keys_tab_bounds, fallback_xy=(360, 1218))
                nxt = self._wait_for_states(
                    {ScreenState.SCREEN_REDEEM_KEYS},
                    timeout=5.0,
                    poll_interval=0.12,
                    initial_delay=0.10,
                )
                if nxt:
                    self._prefetched_info = nxt
                return

        # Keys > 0 -> Select a door card randomly
        doors = info.get("door_cards", [])
        if not doors:
            doors = [
                {"name": "The Prophet's", "center": self._scale_xy(183, 600)},
                {"name": "The Quran", "center": self._scale_xy(536, 600)},
                {"name": "Makkah and the", "center": self._scale_xy(183, 983)},
                {"name": "Madinah and the", "center": self._scale_xy(536, 983)},
            ]

        chosen_door = random.choice(doors)
        self.last_chosen_door_name = chosen_door["name"]
        self._cycle_start_time = time.monotonic()
        self._cycle_timings = {}
        self.update_telemetry_status(f"Selecting door: '{chosen_door['name']}'...")
        t0 = time.monotonic()
        cx, cy = chosen_door["center"]
        self.d.click(cx, cy)

        nxt = self._wait_for_states(
            {ScreenState.SCREEN_DOOR_CONFIRM_POPUP},
            timeout=4.0,
            poll_interval=0.10,
            initial_delay=0.08,
        )
        self._cycle_timings["door_ms"] = int((time.monotonic() - t0) * 1000)
        if nxt:
            self._prefetched_info = nxt

    def _handle_screen_door_confirm_popup(self, info: Dict[str, Any]):
        confirm_data = info.get("door_confirm_data") or {}
        if self._stop_requested or self.active_mode == "redeem_only":
            self.update_telemetry_status("Closing door confirmation popup (Stop requested or Redeem mode)...")
            self._click_bounds(confirm_data.get("close_bounds"), fallback_xy=(360, 1137))
            nxt = self._wait_for_states(
                {ScreenState.SCREEN_CHOOSE_DOOR},
                timeout=3.5,
                poll_interval=0.12,
                initial_delay=0.08,
            )
            if nxt:
                self._prefetched_info = nxt
            return

        self.update_telemetry_status(f"Confirming 'Open the door' for '{getattr(self, 'last_chosen_door_name', 'Category')}'...")
        # Start timer for question cycle duration (from 'Open the door' to 'SCREEN_QUIZ_RESULT')
        self.question_start_time = time.time()
        t0 = time.monotonic()
        self._click_bounds(confirm_data.get("open_door_bounds"), fallback_xy=(360, 1044))

        nxt = self._wait_for_states(
            {ScreenState.SCREEN_QUIZ_QUESTION},
            timeout=7.0,
            poll_interval=0.12,
            initial_delay=0.12,
            accept_fn=lambda inf: bool(
                (inf.get("question_data") or {}).get("question")
                and (inf.get("question_data") or {}).get("options")
            ),
        )
        self._cycle_timings["open_ms"] = int((time.monotonic() - t0) * 1000)
        if nxt:
            self._prefetched_info = nxt

    def _fail_fatal(self, category: str, message: str):
        """Logic-level fatal error: stop THIS worker only, status ERROR (needs operator attention)."""
        self.log_system(LogSeverity.CRITICAL, category, message)
        self._alert("error", message)
        self.update_telemetry_status(message, bot_status="ERROR")
        self.final_status = "ERROR"
        self._stop_requested = True
        self._stop_event.set()

    def _handle_screen_quiz_question(self, info: Dict[str, Any]):
        q_data = info.get("question_data") or {}
        question = q_data.get("question", "")
        options = q_data.get("options", [])

        if not question or not options:
            self.update_telemetry_status("Waiting for question & options to render...")
            self._sleep(0.15)
            return

        opt_texts = [o["text"] for o in options]
        self.update_telemetry_status(f"Solving: '{question[:45]}...' ({len(opt_texts)} options)")

        # Strict Zero-Guess Policy: NEVER click a guessed answer if the AI fails.
        try:
            start_solve = time.time()
            ai_res = quiz_solver.solve_quiz(question, opt_texts)
            duration_ms = int((time.time() - start_solve) * 1000)
        except AISolverError as e:
            self._fail_fatal("AI_FAILURE", f"AI Solver Error: {e}")
            return
        except Exception as e:
            self._fail_fatal("AI_FAILURE", f"Unexpected AI error: {e}")
            return

        chosen_idx = ai_res["selected_index"]
        chosen_ans = ai_res["selected_answer"]
        ai_ms = ai_res.get("ai_duration_ms")
        if ai_ms is not None:
            self._cycle_timings["ai_ms"] = ai_ms

        self.last_quiz_context = {
            "door": self.last_chosen_door_name, "question": question, "options": opt_texts,
            "chosen_answer": chosen_ans, "chosen_index": chosen_idx, "duration_ms": duration_ms,
            "ai_duration_ms": ai_ms,
        }

        if self._stop_requested:
            return

        t_submit = time.monotonic()
        if 0 <= chosen_idx < len(options):
            cx, cy = options[chosen_idx]["center"]
            self.update_telemetry_status(f"Clicking option [{chosen_idx}]: '{chosen_ans}'...")
            self.d.click(cx, cy)

        if self._stop_requested:
            return

        # Conditional Swipe: if "Check your answer" is already visible inside viewport,
        # skip swipe completely and click directly after a brief Compose selection settle.
        confirm_bounds = q_data.get("confirm_bounds")
        swiped = False
        if self._in_viewport(confirm_bounds):
            if not self._sleep(0.10):
                return
            self.update_telemetry_status("Submitting answer ('Check your answer')...")
            self._click_bounds(confirm_bounds, fallback_xy=(360, 1201))
        else:
            swiped = True
            if not self._sleep(0.08):
                return
            self._swipe_rel(0.50, 0.74, 0.50, 0.35, 0.12)
            swiped_info = self._wait_for_states(
                {ScreenState.SCREEN_QUIZ_QUESTION, ScreenState.SCREEN_QUIZ_RESULT},
                timeout=1.5,
                poll_interval=0.10,
                initial_delay=0.10,
                accept_fn=lambda inf: (
                    inf["state"] == ScreenState.SCREEN_QUIZ_RESULT
                    or self._in_viewport((inf.get("question_data") or {}).get("confirm_bounds"))
                ),
            )
            if self._stop_requested:
                return
            if swiped_info and swiped_info["state"] in (ScreenState.SCREEN_QUIZ_RESULT, ScreenState.SCREEN_DAILY_LIMIT_POPUP):
                if swiped_info["state"] == ScreenState.SCREEN_QUIZ_RESULT:
                    self._cycle_timings["submit_ms"] = int((time.monotonic() - t_submit) * 1000)
                self._prefetched_info = swiped_info
                return
            fresh_confirm = ((swiped_info or {}).get("question_data") or {}).get("confirm_bounds")
            self.update_telemetry_status("Submitting answer ('Check your answer')...")
            self._click_bounds(fresh_confirm, fallback_xy=(360, 1201))

        self._cycle_timings["swiped"] = 1 if swiped else 0

        # Smart Wait for SCREEN_QUIZ_RESULT + Guard #3:
        # If button wasn't enabled yet or shifted after selecting option, re-click fresh bounds once.
        if not self._sleep(0.12):
            return
        deadline = time.monotonic() + 5.5
        submit_clicked_at = time.monotonic()
        retried_submit = False

        while time.monotonic() < deadline:
            if self._stop_requested:
                return
            post_info = self._inspect_screen(max_retries=0)
            st = post_info["state"]
            if st == ScreenState.SCREEN_QUIZ_RESULT:
                self._cycle_timings["submit_ms"] = int((time.monotonic() - t_submit) * 1000)
                self._prefetched_info = post_info
                return
            if st in (ScreenState.SCREEN_DAILY_LIMIT_POPUP, ScreenState.SCREEN_LAUNCHER_OR_CLOSED) or self.d is None:
                self._prefetched_info = post_info
                return
            if st == ScreenState.SCREEN_QUIZ_QUESTION and not retried_submit and (time.monotonic() - submit_clicked_at) >= 0.85:
                fresh_bounds = (post_info.get("question_data") or {}).get("confirm_bounds")
                if self._in_viewport(fresh_bounds):
                    self.update_telemetry_status("Retrying 'Check your answer' with fresh bounds...")
                    self._click_bounds(fresh_bounds)
                else:
                    self._swipe_rel(0.50, 0.74, 0.50, 0.35, 0.12)
                    if not self._sleep(0.12):
                        return
                    re_info = self._inspect_screen(max_retries=0)
                    if re_info["state"] in (ScreenState.SCREEN_QUIZ_RESULT, ScreenState.SCREEN_DAILY_LIMIT_POPUP):
                        if re_info["state"] == ScreenState.SCREEN_QUIZ_RESULT:
                            self._cycle_timings["submit_ms"] = int((time.monotonic() - t_submit) * 1000)
                        self._prefetched_info = re_info
                        return
                    re_bounds = (re_info.get("question_data") or {}).get("confirm_bounds")
                    self._click_bounds(re_bounds, fallback_xy=(360, 1201))
                retried_submit = True
                submit_clicked_at = time.monotonic()
            if not self._sleep(0.12):
                return

    def _handle_screen_quiz_result(self, info: Dict[str, Any]):
        res_data = info.get("result_data") or {}
        is_correct = res_data.get("is_correct", False)

        cycle_duration_ms = 0
        if self.question_start_time is not None:
            cycle_duration_ms = int((time.time() - self.question_start_time) * 1000)
            self.question_start_time = None

        t = self._cycle_timings
        timing_parts = []
        if "door_ms" in t:
            timing_parts.append(f"door:{t['door_ms']}ms")
        if "open_ms" in t:
            timing_parts.append(f"open:{t['open_ms']}ms")
        if "submit_ms" in t:
            timing_parts.append(f"submit:{t['submit_ms']}ms")
        if "swiped" in t:
            timing_parts.append("swipe:Y" if t["swiped"] else "swipe:N")
        timing_detail = ", ".join(timing_parts) if timing_parts else None

        if self.last_quiz_context:
            ctx = self.last_quiz_context
            final_duration_ms = cycle_duration_ms if cycle_duration_ms > 0 else ctx.get("duration_ms", 0)
            self._record_quiz_log(
                ctx["door"], ctx["question"], ctx["options"], ctx["chosen_answer"],
                ctx["chosen_index"], is_correct, final_duration_ms, ctx.get("ai_duration_ms"),
                timing_detail=timing_detail,
            )
            self.last_quiz_context = None

        if self.active_mode in ("answer_only", "auto"):
            self.session_completed_count += 1
            instance_manager.set_runtime(self.uuid, session_completed_count=self.session_completed_count)
            instance_manager.update_task_progress(self.uuid, self.session_completed_count)
            if self.stop_mode == "limit" and self.session_completed_count >= self.stop_limit:
                msg = f"Target stop condition tercapai: {self.session_completed_count}/{self.stop_limit} soal berhasil dijawab. Bot berhenti elegan."
                self.log_system(LogSeverity.INFO, "BOT_CONTROL", msg)
                self.update_telemetry_status(msg, bot_status="IDLE")
                self._alert("info", msg)
                instance_manager.clear_task_intent(self.uuid, "Target limit reached")
                self._stopped_reason = "LIMIT_REACHED"
                self._stop_requested = True
                return

        self.update_telemetry_status(f"Quiz result: {'CORRECT' if is_correct else 'INCORRECT'}. Clicking 'Play again'...")
        t_play = time.monotonic()
        self._click_bounds(res_data.get("play_again_bounds"), fallback_xy=(360, 1124))
        nxt = self._wait_for_states(
            {ScreenState.SCREEN_CHOOSE_DOOR},
            timeout=5.0,
            poll_interval=0.12,
            initial_delay=0.10,
        )
        if self._cycle_start_time is not None:
            full_ms = int((time.monotonic() - self._cycle_start_time) * 1000)
            res_ms = int((time.monotonic() - t_play) * 1000)
            logger.info(
                f"Worker [{self.name}] Full quiz cycle: {full_ms / 1000.0:.2f}s "
                f"(result->door:{res_ms}ms, {timing_detail or ''})"
            )
            self._cycle_start_time = None
        if nxt:
            self._prefetched_info = nxt

    def _clear_redeem_input(self, info: Optional[Dict[str, Any]] = None):
        """Fast input cleanup after a rejected key, avoiding redundant clicks and adb subprocesses."""
        try:
            self.last_inspect_info = None
            if not self.d:
                return
            redeem_data = (info or {}).get("redeem_data") or {}
            # Guard #2: Clear only if EditText actually holds text (or if info wasn't supplied)
            should_clear = True if info is None else bool(redeem_data.get("input_has_value", True))
            if should_clear:
                edit = self.d(className="android.widget.EditText")
                if edit.exists:
                    edit.clear_text(timeout=2.0)
                    self._sleep(0.08)
            if info is None or info.get("has_ime_overlay"):
                self._press_hide_keyboard()
                self._sleep(0.12)
        except Exception as e:
            logger.debug(f"Error clearing redeem input on {self.device_id}: {e}")

    def _handle_screen_redeem_keys(self, info: Dict[str, Any]):
        redeem_data = info.get("redeem_data") or {}
        error_msg = redeem_data.get("error_msg", "")
        is_already_used = redeem_data.get("is_already_used", False)
        is_invalid = redeem_data.get("is_invalid", False)

        # Rejection from the Nusuk app for a previously typed key
        if (is_already_used or is_invalid or error_msg) and self.current_claim_id:
            self.redeem_start_time = None
            if is_already_used or ("already" in error_msg.lower() and "used" in error_msg.lower()):
                note = error_msg or "Already used in Nusuk"
                self.log_system(LogSeverity.INFO, "KEY_LIFECYCLE",
                                f"Key '{self.current_key_code}' sudah pernah digunakan ({note}). Menandai status key sebagai USED (bukan invalid).")
                self._finish_claim(ClaimStatus.USED, note=note)
            else:
                note = error_msg or "Invalid key"
                self.log_system(LogSeverity.WARNING, "KEY_LIFECYCLE",
                                f"Key '{self.current_key_code}' ditolak Nusuk: {note}. Menandai status key sebagai INVALID & blacklist global.")
                self._finish_claim(ClaimStatus.INVALID, note=note)
            self._clear_redeem_input(info)
            return

        if self._stop_requested:
            self._safe_rollback_key()
            return

        if self.redeem_start_time is None:
            self.redeem_start_time = time.time()

        # Ensure EditText is visible BEFORE locking a key in DB.
        # When extra notices appear under the key count (e.g. "One key will expire in X hours",
        # "You do not have any key"), the EditText is pushed below the fold -> scroll DOWN (swipe UP).
        if not redeem_data.get("has_input"):
            scroll_attempts = [
                (0.50, 0.74, 0.50, 0.30, 0.20),  # scroll down (reveal below fold)
                (0.50, 0.74, 0.50, 0.30, 0.20),  # scroll down further if tall notice
                (0.50, 0.33, 0.50, 0.69, 0.20),  # fallback scroll up in case overshot
            ]
            for fx, fy, tx, ty, dur in scroll_attempts:
                if self._stop_requested:
                    return
                self._swipe_rel(fx, fy, tx, ty, dur)
                scrolled = self._wait_for_states(
                    {ScreenState.SCREEN_REDEEM_KEYS},
                    timeout=1.2,
                    poll_interval=0.10,
                    initial_delay=0.12,
                    accept_fn=lambda inf: bool((inf.get("redeem_data") or {}).get("has_input")),
                )
                if scrolled:
                    info = scrolled
                    redeem_data = info.get("redeem_data") or {}
                    break

        edit = self.d(className="android.widget.EditText")
        if not redeem_data.get("has_input") and not edit.exists:
            self.log_system(LogSeverity.ERROR, "KEY_REDEEM", "EditText for key input not found on screen after scrolling")
            self._sleep(0.3)
            return

        key_obj = self._lock_key()
        if not key_obj:
            msg = "Selesai: Stok key eligible untuk device ini telah habis."
            self.log_system(LogSeverity.INFO, "BOT_CONTROL", msg)
            self.update_telemetry_status(msg, bot_status="IDLE")
            self._alert("info", msg)
            self._stop_requested = True
            return

        code = key_obj["code"]
        t_input = time.monotonic()
        self.update_telemetry_status(f"Inputting key '{code}'...")

        # Guard #2: Smart clear_text — only clear when XML shows leftover typed value
        # (e.g., from a previously rejected key), avoiding unnecessary click+clear RPCs on empty fields.
        try:
            if redeem_data.get("input_has_value"):
                edit.clear_text(timeout=2.0)
                if not self._sleep(0.08):
                    self._safe_rollback_key()
                    return
            edit.set_text(code, timeout=2.0)
        except Exception as e:
            logger.warning(f"Direct set_text failed on {self.device_id} ({e}); retrying with focus click")
            self._click_bounds(redeem_data.get("input_bounds"))
            if not self._sleep(0.10):
                self._safe_rollback_key()
                return
            edit.clear_text(timeout=2.0)
            edit.set_text(code, timeout=2.0)

        if not self._sleep(0.10):
            self._safe_rollback_key()
            return

        # Guard #3: Re-dump once after set_text so we read post-typing layout and check whether
        # soft keyboard appeared or shifted "Check the code". Skip hide_keyboard when unobstructed!
        after_type = self._inspect_screen(max_retries=0)
        after_redeem = after_type.get("redeem_data") or {}
        check_bounds = after_redeem.get("check_code_bounds")
        has_ime = bool(after_type.get("has_ime_overlay"))

        if has_ime or not self._in_viewport(check_bounds):
            if has_ime:
                self._press_hide_keyboard()
                if not self._sleep(0.15):
                    self._safe_rollback_key()
                    return
                after_type = self._inspect_screen(max_retries=0)
                after_redeem = after_type.get("redeem_data") or {}
                check_bounds = after_redeem.get("check_code_bounds")

            if not self._in_viewport(check_bounds):
                self._swipe_rel(0.50, 0.74, 0.50, 0.47, 0.12)
                if not self._sleep(0.12):
                    self._safe_rollback_key()
                    return
                after_type = self._inspect_screen(max_retries=0)
                after_redeem = after_type.get("redeem_data") or {}
                check_bounds = after_redeem.get("check_code_bounds")

        if self._stop_requested:
            self._safe_rollback_key()
            return

        # Anti-Stale baseline: capture any pre-existing error before submitting
        pre_submit_err = after_redeem.get("error_msg") or redeem_data.get("error_msg") or ""

        self.update_telemetry_status(f"Clicking 'Check the code' for '{code}'...")
        self._click_bounds(check_bounds, fallback_xy=(360, 1000))
        input_ms = int((time.monotonic() - t_input) * 1000)

        # Smart Wait for redeem result (SCREEN_REDEEM_SUCCESS or explicit app rejection)
        if not self._sleep(0.15):
            return
        t_verify = time.monotonic()
        retried_check = False

        while time.monotonic() - t_verify < 10.0:
            if self._stop_requested:
                return  # run-loop finally performs the safe rollback

            sub_info = self._inspect_screen(max_retries=0)
            st = sub_info["state"]

            if st == ScreenState.SCREEN_REDEEM_SUCCESS:
                verify_ms = int((time.monotonic() - t_verify) * 1000)
                redeem_dur = None
                if self.redeem_start_time is not None:
                    redeem_dur = int((time.time() - self.redeem_start_time) * 1000)
                    self.redeem_start_time = None
                self._finish_claim(
                    ClaimStatus.USED,
                    note="Redeem success",
                    duration_ms=redeem_dur,
                    timing_detail=f"in:{input_ms}ms, verify:{verify_ms}ms",
                )
                self._prefetched_info = sub_info
                return

            if st == ScreenState.SCREEN_LAUNCHER_OR_CLOSED:
                self._prefetched_info = sub_info
                break

            sub_redeem = sub_info.get("redeem_data") or {}
            sub_err = sub_redeem.get("error_msg", "")
            sub_is_used = sub_redeem.get("is_already_used", False)
            sub_is_invalid = sub_redeem.get("is_invalid", False)
            elapsed = time.monotonic() - t_verify

            # Anti-Stale check: if sub_err equals pre_submit_err within first 2.0s, keep polling
            if sub_is_used or sub_is_invalid or sub_err:
                if pre_submit_err and sub_err == pre_submit_err and elapsed < 2.0:
                    logger.debug(f"Ignoring potentially stale error text '{sub_err}' ({elapsed:.1f}s elapsed)")
                    if not self._sleep(0.12):
                        return
                    continue

            if sub_is_used or ("already" in sub_err.lower() and "used" in sub_err.lower()):
                self.redeem_start_time = None
                note = sub_err or "Already used in Nusuk"
                self.log_system(LogSeverity.INFO, "KEY_REDEEM",
                                f"Key '{code}' sudah pernah digunakan ({note}). Menandai status key sebagai USED (bukan invalid).")
                self.update_telemetry_status(f"Key '{code}' already used. Marking USED & clearing input...", bot_status="RUNNING")
                self._finish_claim(ClaimStatus.USED, note=note)
                self._clear_redeem_input(sub_info)
                return

            if sub_is_invalid or sub_err:
                self.redeem_start_time = None
                note = sub_err or "Invalid key"
                self.log_system(LogSeverity.WARNING, "KEY_REDEEM",
                                f"Key '{code}' ditolak Nusuk ({note}). Menandai status key sebagai INVALID & blacklist global.")
                self.update_telemetry_status(f"Key '{code}' invalid ({note}). Marking INVALID & clearing input...", bot_status="RUNNING")
                self._finish_claim(ClaimStatus.INVALID, note=note)
                self._clear_redeem_input(sub_info)
                return

            # Guard #3: If button shifted right before click and still idle on redeem screen, retry once
            if st == ScreenState.SCREEN_REDEEM_KEYS and not retried_check and elapsed >= 1.2:
                fresh_check = sub_redeem.get("check_code_bounds")
                if self._in_viewport(fresh_check):
                    logger.debug(f"Retrying 'Check the code' click once with fresh bounds on {self.device_id}")
                    self._click_bounds(fresh_check)
                    retried_check = True

            if st == ScreenState.SCREEN_UNKNOWN_OR_LOADING:
                self._check_still_online(force=False)

            if not self._sleep(0.12):
                return

        # UI timeout / emulator lag (NOT an explicit rejection) -> Safe Rollback, never blacklist.
        self.redeem_start_time = None
        kid = self.current_key_id
        self._safe_rollback_key()
        if kid is not None:
            self.key_timeout_counts[kid] = self.key_timeout_counts.get(kid, 0) + 1
            if self.key_timeout_counts[kid] >= 2:
                self._fail_fatal("KEY_REDEEM", f"Redeem timeout berulang pada key ID {kid}; bot dihentikan (key tidak di-blacklist).")
                return
        self._execute_auto_recovery("Redeem response timeout (key di-rollback, bukan blacklist)")

    def _handle_screen_redeem_success(self, info: Dict[str, Any]):
        self.update_telemetry_status("Redeem Congratulations screen reached! Key redeemed.")
        if self.current_claim_id:
            redeem_dur = None
            if self.redeem_start_time is not None:
                redeem_dur = int((time.time() - self.redeem_start_time) * 1000)
                self.redeem_start_time = None
            self._finish_claim(ClaimStatus.USED, note="Congratulations confirmed", duration_ms=redeem_dur)

        if self._stop_requested:
            return

        success_data = info.get("success_data") or {}

        if self.active_mode == "redeem_only":
            remaining = key_service.eligible_count(self.uuid)
            if remaining > 0:
                self.update_telemetry_status(f"{remaining} key eligible tersisa -> Clicking 'Add another code'...")
                self.redeem_start_time = time.time()
                self._click_bounds(success_data.get("add_another_bounds"), fallback_xy=(360, 1140))
                nxt = self._wait_for_states(
                    {ScreenState.SCREEN_REDEEM_KEYS},
                    timeout=5.0,
                    poll_interval=0.12,
                    initial_delay=0.10,
                )
                if nxt:
                    self._prefetched_info = nxt
            else:
                msg = "Selesai: Seluruh stok key eligible untuk device ini telah habis."
                self.log_system(LogSeverity.INFO, "BOT_CONTROL", msg)
                self.update_telemetry_status(msg, bot_status="IDLE")
                self._alert("info", msg)
                self._stop_requested = True
            return

        if self.active_mode == "auto":
            self.update_telemetry_status("Key redeemed in Auto mode -> Clicking 'Play again' to resume quiz...")
            play_target = success_data.get("play_again_bounds") or success_data.get("play_tab_bounds")
            self._click_bounds(play_target, fallback_xy=(72, 1218))
            nxt = self._wait_for_states(
                {ScreenState.SCREEN_CHOOSE_DOOR},
                timeout=5.0,
                poll_interval=0.12,
                initial_delay=0.10,
            )
            if nxt:
                self._prefetched_info = nxt

    # ------------------------------------------------------------------ AUTO-LOGIN HANDLERS
    def _handle_screen_welcome_slides(self, info: Dict[str, Any]):
        """Auto-advance Nusuk onboarding Welcome Slides (Slides 1..5 -> Login Main)."""
        self._set_login_state(
            LoginStatus.LOGGED_OUT,
            step="Melewati Welcome Screen (Auto-Login)...",
            clear_error=True,
        )
        welcome_data = info.get("welcome_data") or {}
        self._click_bounds(welcome_data.get("next_bounds"), fallback_xy=(612, 1169))
        # Give Compose pager a short moment to slide to the next page or SCREEN_LOGIN_MAIN
        if not self._sleep(0.35):
            return
        nxt = self._inspect_screen(max_retries=0)
        if nxt["state"] != ScreenState.SCREEN_UNKNOWN_OR_LOADING:
            self._prefetched_info = nxt

    def _wait_for_login_event(self, event: threading.Event, expected_states: set, poll_step: float = 0.35) -> str:
        """
        Interruptible wait loop while waiting for operator credentials or OTP via FastAPI.
        Returns: 'received' | 'screen_changed' | 'stopped'.
        """
        last_screen_check = time.monotonic()
        while not self._stop_requested:
            if event.is_set():
                return "received"
            if self._stop_event.wait(timeout=poll_step):
                return "stopped"
            if event.is_set():
                return "received"

            # Periodically re-check ADB online & screen in case operator acted manually on emulator
            now = time.monotonic()
            if now - last_screen_check >= 3.5:
                last_screen_check = now
                fresh = self._inspect_screen(max_retries=0)
                st = fresh["state"]
                if st == ScreenState.SCREEN_UNKNOWN_OR_LOADING and not self.d:
                    self._check_still_online(force=True)
                elif st != ScreenState.SCREEN_UNKNOWN_OR_LOADING and st not in expected_states:
                    self._prefetched_info = fresh
                    return "screen_changed"
        return "stopped"

    def _ensure_remember_me_off(self, info: Dict[str, Any]) -> Dict[str, Any]:
        """Verify 'Remember me' toggle state from XML and disable it if ON."""
        for attempt in range(2):
            login_data = info.get("login_data") or {}
            rem = login_data.get("remember_me") or {}
            if not rem.get("found"):
                logger.debug(f"Worker [{self.name}] Remember me toggle node not detected in XML")
                return info
            if not rem.get("checked"):
                self.log_system(
                    LogSeverity.INFO,
                    "AUTO_LOGIN",
                    f"Toggle 'Remember me' terverifikasi OFF ({rem.get('source', 'xml')}).",
                )
                return info

            self.log_system(
                LogSeverity.INFO,
                "AUTO_LOGIN",
                f"Toggle 'Remember me' terdeteksi ON — mematikan secara otomatis (percobaan #{attempt + 1})...",
            )
            self._set_login_state(LoginStatus.PROCESSING_LOGIN, step="Mematikan toggle 'Remember me'...")
            self._click_bounds(rem.get("bounds"), fallback_xy=(78, 796))
            if not self._sleep(0.25):
                return info
            info = self._inspect_screen(max_retries=0)
        return info

    def _set_edit_text_safe(self, instance_idx: int, value: str, bounds: Optional[tuple], mask_in_logs: bool = False) -> bool:
        """Set text into a Jetpack Compose EditText via uiautomator2 .set_text() with focus fallback."""
        if not self.d:
            return False
        display_val = "****" if mask_in_logs else value
        try:
            el = self.d(className="android.widget.EditText", instance=instance_idx)
            if el.exists:
                el.set_text(value, timeout=3.0)
                return True
        except Exception as e:
            logger.debug(f"Worker [{self.name}] Direct set_text(instance={instance_idx}, val={display_val}) failed: {e}")

        # Fallback: tap field bounds first, then clear & set_text
        try:
            if bounds:
                self._click_bounds(bounds)
                if not self._sleep(0.15):
                    return False
            el = self.d(className="android.widget.EditText", instance=instance_idx)
            if el.exists:
                try:
                    el.clear_text(timeout=2.0)
                except Exception:
                    pass
                el.set_text(value, timeout=3.0)
                return True
        except Exception as e:
            logger.warning(f"Worker [{self.name}] Fallback set_text(instance={instance_idx}, val={display_val}) failed: {e}")
        return False

    def _fill_credentials_and_submit(self, info: Dict[str, Any], email: str, password: str):
        """Navigate to credentials form (if needed), type email & password, ensure Remember me OFF, click Login."""
        # SECURITY: NEVER log `password` anywhere!
        self._set_login_state(
            LoginStatus.PROCESSING_LOGIN,
            step=f"Memulai Auto-Login untuk {email}...",
            clear_error=True,
        )
        self.log_system(LogSeverity.INFO, "AUTO_LOGIN", f"Memproses Auto-Login untuk email '{email}' (password: ****)")

        state = info["state"]

        # Step 1: From SCREEN_LOGIN_MAIN -> click "Login Or Register"
        if state == ScreenState.SCREEN_LOGIN_MAIN:
            self._set_login_state(LoginStatus.PROCESSING_LOGIN, step="Menekan 'Login Or Register'...")
            ld = info.get("login_data") or {}
            self._click_bounds(ld.get("login_or_register_bounds"), fallback_xy=(360, 1107))
            nxt = self._wait_for_states(
                {ScreenState.SCREEN_LOGIN_METHOD_POPUP, ScreenState.SCREEN_CREDENTIALS_FORM},
                timeout=6.0,
                poll_interval=0.15,
                initial_delay=0.15,
            )
            if self._stop_requested:
                return
            if nxt:
                info = nxt
                state = info["state"]
            else:
                info = self._inspect_screen(max_retries=1)
                state = info["state"]

        # Step 2: From SCREEN_LOGIN_METHOD_POPUP or SCREEN_LOGGED_OUT_TIMED_OUT -> click "Login with Email"
        if state in (ScreenState.SCREEN_LOGIN_METHOD_POPUP, ScreenState.SCREEN_LOGGED_OUT_TIMED_OUT):
            self._set_login_state(LoginStatus.PROCESSING_LOGIN, step="Memilih metode 'Login with Email'...")
            ld = info.get("login_data") or {}
            self._click_bounds(ld.get("login_with_email_bounds"), fallback_xy=(195, 753))
            nxt = self._wait_for_states(
                {ScreenState.SCREEN_CREDENTIALS_FORM},
                timeout=7.0,
                poll_interval=0.15,
                initial_delay=0.15,
            )
            if self._stop_requested:
                return
            if nxt:
                info = nxt
                state = info["state"]
            else:
                info = self._inspect_screen(max_retries=1)
                state = info["state"]

        if state != ScreenState.SCREEN_CREDENTIALS_FORM:
            err_msg = f"Gagal membuka form Email & Password (layar saat ini: {state}). Silakan coba lagi."
            self.log_system(LogSeverity.WARNING, "AUTO_LOGIN", err_msg)
            self._set_login_state(LoginStatus.AWAITING_CREDENTIALS, step=err_msg, error=err_msg)
            self._prefetched_info = info
            return

        # Step 3: Fill Email & Password on SCREEN_CREDENTIALS_FORM
        ld = info.get("login_data") or {}
        pre_submit_err = ld.get("error_msg") or ""

        self._set_login_state(LoginStatus.PROCESSING_LOGIN, step="Mengisi Email Address...")
        if not self._set_edit_text_safe(0, email, ld.get("email_edit_bounds"), mask_in_logs=False):
            err_msg = "Gagal mengisi kolom Email Address di aplikasi Nusuk."
            self.log_system(LogSeverity.ERROR, "AUTO_LOGIN", err_msg)
            self._set_login_state(LoginStatus.AWAITING_CREDENTIALS, step=err_msg, error=err_msg)
            return

        if not self._sleep(0.15):
            return

        self._set_login_state(LoginStatus.PROCESSING_LOGIN, step="Mengisi Password...")
        if not self._set_edit_text_safe(1, password, ld.get("password_edit_bounds"), mask_in_logs=True):
            err_msg = "Gagal mengisi kolom Password di aplikasi Nusuk."
            self.log_system(LogSeverity.ERROR, "AUTO_LOGIN", err_msg)
            self._set_login_state(LoginStatus.AWAITING_CREDENTIALS, step=err_msg, error=err_msg)
            return

        if not self._sleep(0.20):
            return

        # Step 4: Hide IME keyboard if covering toggle/Login button, then refresh XML
        fresh = self._inspect_screen(max_retries=0)
        fresh_ld = fresh.get("login_data") or {}
        if fresh.get("has_ime_overlay") or not self._in_viewport(fresh_ld.get("login_btn_bounds")):
            self._press_hide_keyboard()
            if not self._sleep(0.25):
                return
            fresh = self._inspect_screen(max_retries=0)

        if self._stop_requested:
            return

        # Step 5: Ensure 'Remember me' toggle is OFF
        fresh = self._ensure_remember_me_off(fresh)
        if self._stop_requested:
            return

        # Step 6: Click 'Login' button
        fresh_ld = fresh.get("login_data") or {}
        login_bounds = fresh_ld.get("login_btn_bounds")
        self._set_login_state(LoginStatus.PROCESSING_LOGIN, step="Menekan tombol Login — menunggu verifikasi server...")
        self._click_bounds(login_bounds, fallback_xy=(360, 913))

        # Step 7: Smart wait for OTP screen, Dashboard, or Credentials Error
        if not self._sleep(0.4):
            return
        t_wait = time.monotonic()
        retried_click = False

        while time.monotonic() - t_wait < 20.0:
            if self._stop_requested:
                return
            sub_info = self._inspect_screen(max_retries=0)
            st = sub_info["state"]
            elapsed = time.monotonic() - t_wait

            if st == ScreenState.SCREEN_VERIFY_OTP_FORM:
                self._prefetched_info = sub_info
                self._login_alert_sent_state = None
                self._set_login_state(
                    LoginStatus.AWAITING_OTP,
                    step="Kredensial valid! Menunggu kode OTP 4 digit dari operator...",
                    clear_error=True,
                )
                self.log_system(LogSeverity.INFO, "AUTO_LOGIN", "Kredensial diterima Nusuk — masuk ke layar Verify Email (OTP).")
                return

            if st in AUTHENTICATED_STATES or st == ScreenState.SCREEN_LOCATION_POPUP:
                self._prefetched_info = sub_info
                self._login_alert_sent_state = None
                self._set_login_state(
                    LoginStatus.LOGGED_IN,
                    step="Auto-Login berhasil! Masuk ke Dashboard Nusuk.",
                    clear_error=True,
                )
                self.log_system(LogSeverity.INFO, "AUTO_LOGIN", f"Auto-Login berhasil untuk '{email}' (langsung ke Dashboard).")
                self._alert("success", "Auto-Login berhasil! Melanjutkan otomatisasi.")
                return

            if st == ScreenState.SCREEN_CREDENTIALS_FORM:
                sub_ld = sub_info.get("login_data") or {}
                sub_err = sub_ld.get("error_msg") or ""
                if sub_err:
                    # Anti-stale guard when retrying right after a prior invalid credentials error
                    if pre_submit_err and sub_err == pre_submit_err and elapsed < 2.5:
                        if not self._sleep(0.25):
                            return
                        continue
                    self.log_system(LogSeverity.WARNING, "AUTO_LOGIN", f"Login ditolak Nusuk untuk '{email}': {sub_err}")
                    self._set_login_state(
                        LoginStatus.AWAITING_CREDENTIALS,
                        step=f"Login gagal: {sub_err}",
                        error=sub_err,
                    )
                    self._alert("error", f"Auto-Login gagal: {sub_err}")
                    self._prefetched_info = sub_info
                    return

                # Retry clicking Login button once if still sitting idle on form
                if not retried_click and elapsed >= 3.0 and sub_ld.get("login_btn_enabled", True):
                    self._click_bounds(sub_ld.get("login_btn_bounds"), fallback_xy=(360, 913))
                    retried_click = True

            elif st == ScreenState.SCREEN_UNKNOWN_OR_LOADING:
                self._check_still_online(force=False)

            if not self._sleep(0.25):
                return

        timeout_err = "Respon login Nusuk timeout (20 dtk). Periksa email/password dan coba lagi."
        self.log_system(LogSeverity.WARNING, "AUTO_LOGIN", timeout_err)
        self._set_login_state(LoginStatus.AWAITING_CREDENTIALS, step=timeout_err, error=timeout_err)

    def _handle_screen_awaiting_credentials(self, info: Dict[str, Any]):
        """Wait on Login Main / Method Popup / Session Timed Out / Credentials Form until operator sends credentials."""
        state = info["state"]
        creds = instance_manager.consume_login_credentials(self.uuid)
        if creds:
            self._fill_credentials_and_submit(info, creds["email"], creds["password"])
            return

        rt = instance_manager.rt(self.uuid)
        existing_err = rt.login_error
        if state == ScreenState.SCREEN_CREDENTIALS_FORM:
            xml_err = (info.get("login_data") or {}).get("error_msg") or ""
            if xml_err:
                existing_err = xml_err

        step_map = {
            ScreenState.SCREEN_LOGGED_OUT_TIMED_OUT: "Sesi berakhir (Session Timed Out) — menunggu Auto-Login dari operator...",
            ScreenState.SCREEN_LOGIN_MAIN: "Belum login — menunggu Email & Password (Auto-Login)...",
            ScreenState.SCREEN_LOGIN_METHOD_POPUP: "Pop-up Login terdeteksi — menunggu Email & Password (Auto-Login)...",
            ScreenState.SCREEN_CREDENTIALS_FORM: "Form Login terbuka — menunggu Email & Password dari operator...",
        }
        step_msg = step_map.get(state, "Menunggu Email & Password (Auto-Login)...")

        self._set_login_state(
            LoginStatus.AWAITING_CREDENTIALS,
            step=step_msg,
            error=existing_err,
        )
        if self._login_alert_sent_state != LoginStatus.AWAITING_CREDENTIALS:
            self._login_alert_sent_state = LoginStatus.AWAITING_CREDENTIALS
            self.log_system(LogSeverity.WARNING, "AUTO_LOGIN", f"Perangkat membutuhkan Auto-Login ({state}). Menunggu kredensial dari UI Web.")
            self._alert("warning", "Auto-Login dibutuhkan! Klik tombol 'Auto Login Required' untuk memasukkan Email & Password.")

        wait_res = self._wait_for_login_event(
            rt.credentials_event,
            expected_states={
                ScreenState.SCREEN_LOGIN_MAIN,
                ScreenState.SCREEN_LOGIN_METHOD_POPUP,
                ScreenState.SCREEN_LOGGED_OUT_TIMED_OUT,
                ScreenState.SCREEN_CREDENTIALS_FORM,
            },
        )
        if wait_res == "received" and not self._stop_requested:
            creds = instance_manager.consume_login_credentials(self.uuid)
            if creds:
                fresh = self._inspect_screen(max_retries=1)
                self._fill_credentials_and_submit(fresh, creds["email"], creds["password"])

    def _fill_otp_and_submit(self, info: Dict[str, Any], otp_code: str):
        """Fill the 4 separate OTP EditText boxes (verified from 10/11 XML dumps) and press Verify Your Email."""
        digits = "".join(ch for ch in str(otp_code) if ch.isdigit())
        if len(digits) < 4:
            err_msg = "Kode OTP harus terdiri dari 4 digit angka."
            self._set_login_state(LoginStatus.AWAITING_OTP, step=err_msg, error=err_msg)
            return
        digits = digits[:4]

        self._set_login_state(
            LoginStatus.PROCESSING_LOGIN,
            step="Mengisi kode OTP 4 digit...",
            clear_error=True,
        )
        self.log_system(LogSeverity.INFO, "AUTO_LOGIN", "Menerima kode OTP 4 digit dari operator — mengisi kolom verifikasi...")

        otp_data = info.get("otp_data") or {}
        pre_submit_err = otp_data.get("error_msg") or ""
        otp_inputs = otp_data.get("otp_inputs") or []

        if len(otp_inputs) >= 4:
            for idx in range(4):
                if self._stop_requested:
                    return
                ok = self._set_edit_text_safe(idx, digits[idx], otp_inputs[idx].get("bounds"), mask_in_logs=True)
                if not ok:
                    err_msg = f"Gagal mengisi digit OTP ke-{idx + 1}."
                    self.log_system(LogSeverity.ERROR, "AUTO_LOGIN", err_msg)
                    self._set_login_state(LoginStatus.AWAITING_OTP, step=err_msg, error=err_msg)
                    return
                if not self._sleep(0.08):
                    return
        else:
            # Fallback if future app update consolidates OTP into a single EditText
            first_bounds = otp_inputs[0].get("bounds") if otp_inputs else None
            if not self._set_edit_text_safe(0, digits, first_bounds, mask_in_logs=True):
                err_msg = "Gagal mengisi kolom kode OTP."
                self.log_system(LogSeverity.ERROR, "AUTO_LOGIN", err_msg)
                self._set_login_state(LoginStatus.AWAITING_OTP, step=err_msg, error=err_msg)
                return

        if not self._sleep(0.20):
            return

        # Re-inspect to check keyboard overlay & fresh "Verify Your Email" button bounds
        fresh = self._inspect_screen(max_retries=0)
        fresh_otp = fresh.get("otp_data") or {}
        if fresh.get("has_ime_overlay") or not self._in_viewport(fresh_otp.get("verify_bounds")):
            self._press_hide_keyboard()
            if not self._sleep(0.25):
                return
            fresh = self._inspect_screen(max_retries=0)
            fresh_otp = fresh.get("otp_data") or {}

        if self._stop_requested:
            return

        self._set_login_state(LoginStatus.PROCESSING_LOGIN, step="Menekan 'Verify Your Email' — memverifikasi OTP...")
        self._click_bounds(fresh_otp.get("verify_bounds"), fallback_xy=(360, 855))

        if not self._sleep(0.4):
            return
        t_wait = time.monotonic()
        retried_verify = False

        while time.monotonic() - t_wait < 22.0:
            if self._stop_requested:
                return
            sub_info = self._inspect_screen(max_retries=0)
            st = sub_info["state"]
            elapsed = time.monotonic() - t_wait

            if st in AUTHENTICATED_STATES or st == ScreenState.SCREEN_LOCATION_POPUP:
                self._prefetched_info = sub_info
                self._login_alert_sent_state = None
                self._set_login_state(
                    LoginStatus.LOGGED_IN,
                    step="Auto-Login berhasil! Masuk ke Dashboard Nusuk.",
                    clear_error=True,
                )
                self.log_system(LogSeverity.INFO, "AUTO_LOGIN", "Verifikasi OTP berhasil! Akun kini masuk ke Dashboard Nusuk.")
                self._alert("success", "Auto-Login & verifikasi OTP berhasil! Melanjutkan otomatisasi.")
                return

            if st == ScreenState.SCREEN_VERIFY_OTP_FORM:
                sub_otp = sub_info.get("otp_data") or {}
                sub_err = sub_otp.get("error_msg") or ""
                if sub_err:
                    if pre_submit_err and sub_err == pre_submit_err and elapsed < 2.5:
                        if not self._sleep(0.25):
                            return
                        continue
                    self.log_system(LogSeverity.WARNING, "AUTO_LOGIN", f"Verifikasi OTP ditolak Nusuk: {sub_err}")
                    self._set_login_state(
                        LoginStatus.AWAITING_OTP,
                        step=f"OTP ditolak: {sub_err}",
                        error=sub_err,
                    )
                    self._alert("error", f"Verifikasi OTP gagal: {sub_err}")
                    self._prefetched_info = sub_info
                    return

                if not retried_verify and elapsed >= 3.0 and sub_otp.get("verify_enabled", True):
                    self._click_bounds(sub_otp.get("verify_bounds"), fallback_xy=(360, 855))
                    retried_verify = True

            elif st in (
                ScreenState.SCREEN_LOGGED_OUT_TIMED_OUT,
                ScreenState.SCREEN_LOGIN_MAIN,
                ScreenState.SCREEN_CREDENTIALS_FORM,
            ):
                self._prefetched_info = sub_info
                return

            elif st == ScreenState.SCREEN_UNKNOWN_OR_LOADING:
                self._check_still_online(force=False)

            if not self._sleep(0.25):
                return

        timeout_err = "Verifikasi OTP timeout (22 dtk). Periksa kode OTP dan coba lagi."
        self.log_system(LogSeverity.WARNING, "AUTO_LOGIN", timeout_err)
        self._set_login_state(LoginStatus.AWAITING_OTP, step=timeout_err, error=timeout_err)

    def _handle_screen_verify_otp(self, info: Dict[str, Any]):
        """Wait on Verify Email OTP screen until operator submits 4-digit OTP via UI Web."""
        otp_code = instance_manager.consume_login_otp(self.uuid)
        if otp_code:
            self._fill_otp_and_submit(info, otp_code)
            return

        rt = instance_manager.rt(self.uuid)
        xml_err = (info.get("otp_data") or {}).get("error_msg") or ""
        existing_err = xml_err or rt.login_error

        self._set_login_state(
            LoginStatus.AWAITING_OTP,
            step="Menunggu kode OTP 4 digit dari operator...",
            error=existing_err,
        )
        if self._login_alert_sent_state != LoginStatus.AWAITING_OTP:
            self._login_alert_sent_state = LoginStatus.AWAITING_OTP
            self.log_system(LogSeverity.INFO, "AUTO_LOGIN", "Layar Verify Email aktif — menunggu kode OTP 4 digit dari UI Web.")
            self._alert("warning", "Kode OTP 4 digit dibutuhkan! Masukkan OTP melalui modal Auto-Login.")

        wait_res = self._wait_for_login_event(
            rt.otp_event,
            expected_states={ScreenState.SCREEN_VERIFY_OTP_FORM},
        )
        if wait_res == "received" and not self._stop_requested:
            otp_code = instance_manager.consume_login_otp(self.uuid)
            if otp_code:
                fresh = self._inspect_screen(max_retries=1)
                if fresh["state"] == ScreenState.SCREEN_VERIFY_OTP_FORM:
                    self._fill_otp_and_submit(fresh, otp_code)
                else:
                    self._prefetched_info = fresh

    def _handle_screen_system_dialog(self, info: Dict[str, Any]):
        """
        Handle Android system warning, ANR ('isn't responding'), and crash dialogs
        without destroying worker state or active task intent.
        """
        diag_data = info.get("system_dialog_data") or {}
        diag_title = diag_data.get("title") or "System Dialog"
        diag_msg = diag_data.get("message") or ""
        diag_target = diag_data.get("dialog_target", "unknown")
        is_anr = diag_data.get("is_anr", False)
        is_crash = diag_data.get("is_crash", False)
        wait_bounds = diag_data.get("wait_bounds")
        close_bounds = diag_data.get("close_bounds")
        restart_bounds = diag_data.get("restart_bounds")
        ok_bounds = diag_data.get("ok_bounds")

        # 1. Forensic dump of XML hierarchy
        try:
            dump_res = forensic_service.dump(
                self.uuid,
                self.name,
                category="system_anr" if is_anr else "system_dialog",
                reason=f"{diag_target}_{diag_title}",
                xml_content=getattr(self, "_last_raw_xml", ""),
            )
            if dump_res and dump_res[0]:
                self.log_system(LogSeverity.INFO, "FORENSIC", f"Forensic XML dump tersimpan di: {dump_res[0]}")
        except Exception as fe:
            logger.debug(f"Forensic dump error: {fe}")

        self.log_system(
            LogSeverity.WARNING,
            "SYSTEM_WARNING",
            f"Dialog sistem terdeteksi: '{diag_title}' ({diag_msg}) [target: {diag_target}]. Menjalankan pemulihan non-destruktif...",
        )
        self.update_telemetry_status(f"Menangani dialog sistem: {diag_title}...")

        # 2. Smart recovery strategy
        if diag_target == "system" or (is_anr and wait_bounds and not is_crash):
            # Prioritize 'Wait' / 'Tunggu' button for OS/launcher ANRs so background tasks can catch up
            if wait_bounds:
                self.log_system(LogSeverity.INFO, "SYSTEM_WARNING", "Mencoba menekan tombol 'Wait' / 'Tunggu'...")
                self._click_bounds(wait_bounds)
                if not self._sleep(0.8):
                    return
                fresh = self._inspect_screen(max_retries=1)
                if fresh["state"] != ScreenState.SCREEN_SYSTEM_DIALOG:
                    self.log_system(LogSeverity.INFO, "SYSTEM_WARNING", "Dialog berhasil dilewati via 'Wait'. Melanjutkan otomatisasi.")
                    self._prefetched_info = fresh
                    return

            # If Wait failed or wasn't available, click Close / OK
            btn_clicked = False
            for target_btn in (close_bounds, ok_bounds, restart_bounds):
                if target_btn:
                    self._click_bounds(target_btn)
                    btn_clicked = True
                    break
            if not btn_clicked and self.d:
                try:
                    self.d.press("back")
                except Exception:
                    pass
            if not self._sleep(0.6):
                return
            # Bring Nusuk back to foreground
            adb_manager.launch_app(self.device_id)
        else:
            # Target is Nusuk app itself (e.g. "Nusuk isn't responding" / "Nusuk keeps stopping")
            self._safe_rollback_key()
            btn_clicked = False
            for target_btn in (close_bounds, ok_bounds, restart_bounds):
                if target_btn:
                    self._click_bounds(target_btn)
                    btn_clicked = True
                    break
            if not btn_clicked and self.d:
                try:
                    self.d.press("back")
                except Exception:
                    pass
            if not self._sleep(0.6):
                return

            self.log_system(
                LogSeverity.WARNING,
                "APP_RECOVERY",
                f"Memulihkan aplikasi Nusuk setelah dialog '{diag_title}' (clear RAM & relaunch)...",
            )
            adb_manager.clear_ram(self.device_id)
            self._spoof_adb_status()
            adb_manager.launch_app(self.device_id)

        # Smart wait up to 15s for Nusuk to return to an active screen
        t_wait = time.monotonic()
        while time.monotonic() - t_wait < 15.0:
            if self._stop_requested:
                return
            if not self._sleep(0.5):
                return
            fresh = self._inspect_screen(max_retries=0)
            if fresh["state"] in AUTHENTICATED_STATES or fresh["state"] in LOGIN_FLOW_STATES or fresh["state"] == ScreenState.SCREEN_LOCATION_POPUP:
                self.log_system(LogSeverity.INFO, "APP_RECOVERY", f"Pemulihan berhasil — layar kembali ke: {fresh['state']}")
                self._prefetched_info = fresh
                return

    # ------------------------------------------------------------------ MAIN WORKER LOOP
    def _run_loop(self):
        logger.info(f"Worker [{self.name}] started in mode '{self.active_mode}' on {self.device_id}")
        went_offline = False
        transport_retries = 0
        try:
            if not self.connect_device():
                raise DeviceOffline()
            while not self._stop_requested:
                try:
                    if self._prefetched_info is not None:
                        info = self._prefetched_info
                        self._prefetched_info = None
                    else:
                        info = self._inspect_screen()
                    state = info["state"]

                    if state == ScreenState.SCREEN_UNKNOWN_OR_LOADING and not self.d:
                        self._check_still_online(force=True)

                    if state != ScreenState.SCREEN_UNKNOWN_OR_LOADING:
                        self.consecutive_unknown_count = 0
                        self.consecutive_recoveries = 0
                        self.unknown_first_seen_time = None

                    # Successful step clears consecutive transport retries
                    transport_retries = 0

                    # Confirm LOGGED_IN whenever worker reaches any authenticated screen
                    if state in AUTHENTICATED_STATES:
                        rt = instance_manager.rt(self.uuid)
                        if rt.login_status != LoginStatus.LOGGED_IN or rt.login_error:
                            self._login_alert_sent_state = None
                            self._set_login_state(LoginStatus.LOGGED_IN, clear_error=True)

                    if state == ScreenState.SCREEN_SYSTEM_DIALOG:
                        self._handle_screen_system_dialog(info)
                    elif state == ScreenState.SCREEN_LAUNCHER_OR_CLOSED:
                        self._execute_auto_recovery("Nusuk app was closed or crashed")
                    elif state == ScreenState.SCREEN_WELCOME_SLIDES:
                        self._handle_screen_welcome_slides(info)
                    elif state in (
                        ScreenState.SCREEN_LOGIN_MAIN,
                        ScreenState.SCREEN_LOGIN_METHOD_POPUP,
                        ScreenState.SCREEN_LOGGED_OUT_TIMED_OUT,
                        ScreenState.SCREEN_CREDENTIALS_FORM,
                    ):
                        self._handle_screen_awaiting_credentials(info)
                    elif state == ScreenState.SCREEN_VERIFY_OTP_FORM:
                        self._handle_screen_verify_otp(info)
                    elif state == ScreenState.SCREEN_DAILY_LIMIT_POPUP:
                        self._handle_screen_daily_limit_popup(info)
                    elif state == ScreenState.SCREEN_LOCATION_POPUP:
                        self._handle_screen_location_popup(info)
                    elif state == ScreenState.SCREEN_NUSUK_HOME:
                        self._handle_screen_nusuk_home(info)
                    elif state == ScreenState.SCREEN_CHOOSE_DOOR:
                        self._handle_screen_choose_door(info)
                    elif state == ScreenState.SCREEN_DOOR_CONFIRM_POPUP:
                        self._handle_screen_door_confirm_popup(info)
                    elif state == ScreenState.SCREEN_QUIZ_QUESTION:
                        self._handle_screen_quiz_question(info)
                    elif state == ScreenState.SCREEN_QUIZ_RESULT:
                        self._handle_screen_quiz_result(info)
                    elif state == ScreenState.SCREEN_REDEEM_KEYS:
                        self._handle_screen_redeem_keys(info)
                    elif state == ScreenState.SCREEN_REDEEM_SUCCESS:
                        self._handle_screen_redeem_success(info)
                    elif state == ScreenState.SCREEN_UNKNOWN_OR_LOADING:
                        self._check_still_online(force=False)  # OFFLINE != app error: no recovery, no circuit breaker
                        if self.unknown_first_seen_time is None:
                            self.unknown_first_seen_time = time.time()
                        elapsed_unknown = time.time() - self.unknown_first_seen_time
                        timeout_limit = max(20, settings.loading_timeout_seconds)
                        self.update_telemetry_status(f"Waiting / Screen loading ({int(elapsed_unknown)}s / {timeout_limit}s)...")
                        if elapsed_unknown >= timeout_limit:
                            try:
                                forensic_service.dump(
                                    self.uuid, self.name,
                                    category="screen_timeout",
                                    reason=f"unknown_loading_{int(elapsed_unknown)}s",
                                    xml_content=getattr(self, "_last_raw_xml", ""),
                                )
                            except Exception:
                                pass
                            self._execute_auto_recovery(
                                f"Screen unknown or loading timeout exceeded ({int(elapsed_unknown)}s >= {timeout_limit}s)")
                            self.unknown_first_seen_time = None
                        else:
                            self._sleep(0.4)

                    # Optional user-configured throttle (0.0 = full Smart Wait speed)
                    if settings.action_delay_seconds > 0:
                        self._sleep(settings.action_delay_seconds)

                except Exception as iter_err:
                    if is_transport_or_socket_error(iter_err):
                        transport_retries += 1
                        self.log_system(
                            LogSeverity.WARNING, "CONNECTION",
                            f"Gangguan transport/socket ({iter_err.__class__.__name__}: {iter_err}) — auto-retry in-worker #{transport_retries}/3...",
                        )
                        self._safe_rollback_key()
                        backoff = 1.5 * transport_retries
                        if not self._sleep(backoff):
                            break
                        self.d = None
                        ready = adb_manager.ensure_connected(self.device_id, retries=1, connection_type=self.connection_type)
                        if ready and self.connect_device(retries=2):
                            self.log_system(
                                LogSeverity.INFO, "CONNECTION",
                                f"Koneksi ADB/uiautomator2 berhasil dipulihkan in-worker pada percobaan #{transport_retries}. Melanjutkan tugas.",
                            )
                            transport_retries = 0
                            continue
                        if transport_retries >= 3:
                            self.log_system(
                                LogSeverity.WARNING, "CONNECTION",
                                "Pemulihan transport in-worker gagal setelah 3 percobaan. Mengalihkan ke status OFFLINE untuk auto-reconnect background watchdog.",
                            )
                            went_offline = True
                            break
                    else:
                        raise iter_err

        except DeviceOffline:
            went_offline = True
        except Exception as e:
            # A crashed ADB link looks like an arbitrary exception; classify it before blaming the engine.
            tb_str = traceback.format_exc()
            logger.exception(f"Unexpected error in worker [{self.name}]: {e}\n{tb_str}")
            if is_transport_or_socket_error(e):
                went_offline = True
            else:
                try:
                    if not adb_manager.ensure_connected(self.device_id, retries=1, connection_type=self.connection_type):
                        went_offline = True
                except Exception:
                    went_offline = True
            if not went_offline:
                self.log_system(LogSeverity.ERROR, "BOT_ENGINE", f"Unhandled engine exception: {e}\n{tb_str[:400]}")
                self.final_status = self.final_status or "ERROR"
        finally:
            # Rollback FIRST (DB only, works even when ADB is gone), then scrub any pending login secrets.
            self._safe_rollback_key()
            instance_manager.clear_login_inputs(self.uuid)
            if went_offline:
                self.is_running = False
                self.active_mode = None
                instance_manager.mark_offline(self.uuid, "terputus saat bot berjalan")
            else:
                cur_login_st = instance_manager.rt(self.uuid).login_status
                in_login_flow = cur_login_st in (
                    LoginStatus.LOGGED_OUT,
                    LoginStatus.AWAITING_CREDENTIALS,
                    LoginStatus.AWAITING_OTP,
                    LoginStatus.PROCESSING_LOGIN,
                )
                if self._stop_requested and self._return_to_home_on_stop and not in_login_flow:
                    self._return_to_homepage()
                self.is_running = False
                self.active_mode = None
                if not self._for_switch:
                    if in_login_flow:
                        instance_manager.set_runtime(
                            self.uuid,
                            emit=False,
                            login_status=LoginStatus.LOGGED_OUT,
                            login_error=None,
                        )
                    final = self.final_status or "IDLE"
                    final_step = (
                        self._custom_stop_step or "Bot stopped."
                        if final == "IDLE"
                        else instance_manager.rt(self.uuid).current_step
                    )
                    self.update_telemetry_status(
                        final_step,
                        bot_status=final,
                    )
                    self.log_system(LogSeverity.INFO, "BOT_CONTROL", f"Bot execution finished and status set to {final}.")
                    if final == "IDLE":
                        try:
                            from backend.orchestrator import orchestrator
                            orchestrator._promote_next_queued_task(self.uuid, stopped_reason=self._stopped_reason)
                        except Exception as promo_err:
                            logger.error(f"Error promoting queued task for {self.uuid}: {promo_err}")
