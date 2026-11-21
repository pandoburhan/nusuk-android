import asyncio
import os
import re
import subprocess
import time
import logging
from typing import Optional, Tuple, Dict, List, Any
from backend.config import settings, PROJECT_ROOT, resolve_adb_binary

logger = logging.getLogger(__name__)

ANCHOR_REMOTE_PATH = "/sdcard/.nusuk_device_uuid"
NUSUK_SPLIT_APKS = (
    "base.apk",
    "split_config.x86_64.apk",
    "split_config.xhdpi.apk",
)
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_TCP_SERIAL_RE = re.compile(r"^[^:\s]+:\d{1,5}$")
_MDNS_ADDR_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})")


class ADBManager:
    """
    Stateless, strictly per-target ADB wrapper.
    v2 rule: NO cross-device fallback. Every call needs an explicit `ip:port` serial and only
    ever touches that serial.
    """

    def __init__(self, adb_path: Optional[str] = None):
        self.adb_path = resolve_adb_binary(adb_path or settings.adb_path)
        os.environ["ADBUTILS_ADB_PATH"] = self.adb_path

    # ---------- low level ----------
    def run_adb(self, args: list, timeout: int = 15) -> Tuple[int, str, str]:
        cmd = [self.adb_path] + args
        try:
            res = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                timeout=timeout, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return res.returncode, res.stdout.strip(), res.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", "Timeout expired"
        except Exception as e:
            return -1, "", str(e)

    def shell(self, target: str, command: str, timeout: int = 10) -> Tuple[int, str, str]:
        return self.run_adb(["-s", target, "shell", command], timeout=timeout)

    # ---------- connectivity ----------
    @staticmethod
    def is_tcp_serial(target: Optional[str]) -> bool:
        """True if serial looks like host:port (TCP/IP emulator or Android Wireless Debugging)."""
        return bool(target and _TCP_SERIAL_RE.match(target.strip()))

    @staticmethod
    def classify_serial(serial: str) -> str:
        """Classify an ADB serial from `adb devices -l`: 'tcp' | 'usb'."""
        s = (serial or "").strip()
        if _TCP_SERIAL_RE.match(s) or s.startswith("emulator-"):
            return "tcp"
        return "usb"

    def connect(self, target: str, timeout: int = 6) -> bool:
        if not self.is_tcp_serial(target):
            return self.is_ready(target)
        code, out, err = self.run_adb(["connect", target], timeout=timeout)
        txt = (out + " " + err).lower()
        return "connected to" in txt or "already connected" in txt

    def device_states(self) -> Dict[str, str]:
        """serial -> state from ONE `adb devices` call (cheap; used for fleet heartbeat)."""
        code, out, _ = self.run_adb(["devices"], timeout=8)
        states: Dict[str, str] = {}
        if code == 0:
            for line in out.splitlines()[1:]:
                line = line.strip()
                if line and "\t" in line:
                    serial, state = line.split("\t", 1)
                    states[serial.strip()] = state.strip()
        return states

    def is_ready(self, target: str) -> bool:
        """True only if this exact serial is in `device` state. No fallback, no reconnect."""
        return self.device_states().get(target) == "device"

    def ensure_connected(self, target: str, retries: int = 1, connection_type: Optional[str] = None) -> bool:
        """
        Checks the exact target; never touches other devices.
        - For USB / non-TCP serials: NEVER runs `adb connect`/`adb disconnect`; checks `adb devices` directly.
        - For TCP / Wireless (`ip:port`): tries `adb connect` with self-healing retry.
        """
        if self.is_ready(target):
            return True
        if connection_type == "usb" or not self.is_tcp_serial(target):
            return False
        for attempt in range(retries + 1):
            if self.connect(target):
                if self.is_ready(target):
                    return True
            if attempt < retries:
                time.sleep(1.0)
                if self.is_tcp_serial(target):
                    self.disconnect(target)
                    time.sleep(0.5)
        return self.is_ready(target)

    def get_device_model(self, target: str) -> Optional[str]:
        """Reads friendly manufacturer + model name from an online Android device."""
        code, out, _ = self.shell(
            target,
            "echo \"$(getprop ro.product.manufacturer)|$(getprop ro.product.model)\"",
            timeout=5,
        )
        if code != 0 or not out:
            return None
        parts = out.splitlines()[0].split("|", 1)
        mfg = parts[0].strip() if len(parts) > 0 else ""
        mdl = parts[1].strip() if len(parts) > 1 else ""
        if mdl:
            if mfg and mfg.lower() not in mdl.lower():
                return f"{mfg.capitalize()} {mdl}"
            return mdl
        return mfg.capitalize() if mfg else None

    def discover_devices(self) -> List[Dict[str, Any]]:
        """
        Runs `adb devices -l` and returns metadata for every visible ADB device
        (physical USB phones, emulators, and active wireless ADB connections).
        """
        code, out, _ = self.run_adb(["devices", "-l"], timeout=8)
        items: List[Dict[str, Any]] = []
        if code != 0 or not out:
            return items

        for raw_line in out.splitlines()[1:]:
            line = raw_line.strip()
            if not line or line.startswith("*"):
                continue
            tokens = line.split()
            if len(tokens) < 2:
                continue
            serial = tokens[0].strip()
            state = tokens[1].strip()
            meta: Dict[str, str] = {}
            for tok in tokens[2:]:
                if ":" in tok:
                    k, v = tok.split(":", 1)
                    meta[k.strip()] = v.strip()

            conn_type = self.classify_serial(serial)
            model_raw = meta.get("model", "").replace("_", " ").strip()
            device_code = meta.get("device", "").strip()
            product = meta.get("product", "").strip()
            model_name = model_raw or device_code or product or ""

            if state == "device":
                prop_model = self.get_device_model(serial)
                if prop_model:
                    model_name = prop_model

            ip_val, port_val = None, None
            if self.is_tcp_serial(serial):
                ip_part, port_part = serial.rsplit(":", 1)
                ip_val = ip_part
                try:
                    port_val = int(port_part)
                except ValueError:
                    port_val = None

            items.append({
                "adb_serial": serial,
                "state": state,
                "connection_type": conn_type,
                "is_physical_usb": conn_type == "usb",
                "ip": ip_val,
                "port": port_val,
                "model": model_name or serial,
                "product": product or None,
                "device_code": device_code or None,
            })
        return items

    def pair_wireless(self, ip: str, pairing_port: int, pairing_code: str, timeout: int = 15) -> Tuple[bool, str]:
        """Pairs an Android 11+ Wireless Debugging device via `adb pair <ip>:<pairing_port> <code>`."""
        target = f"{ip.strip()}:{int(pairing_port)}"
        code_clean = (pairing_code or "").strip()
        rc, out, err = self.run_adb(["pair", target, code_clean], timeout=timeout)
        combined = " ".join(p for p in (out, err) if p).strip()
        ok = rc == 0 and ("successfully paired" in combined.lower() or "paired to" in combined.lower())
        if ok:
            return True, combined or f"Berhasil pairing dengan {target}."
        if rc == -1 and "timeout" in combined.lower():
            return False, f"Pairing timeout ({timeout}s). Pastikan layar Wireless Debugging HP masih terbuka dan kode 6 digit belum kedaluwarsa."
        return False, combined or f"Pairing gagal ke {target} (kode keluar {rc})."

    def discover_mdns_connect_ports(self, ip: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Best-effort `adb mdns services` query to auto-detect Android 11+ `_adb-tls-connect._tcp`
        connect port after wireless pairing.
        """
        rc, out, _ = self.run_adb(["mdns", "services"], timeout=4)
        results: List[Dict[str, Any]] = []
        if rc != 0 or not out:
            return results
        target_ip = (ip or "").strip()
        for line in out.splitlines():
            low = line.lower()
            if "_adb-tls-connect._tcp" not in low:
                continue
            m = _MDNS_ADDR_RE.search(line)
            if not m:
                continue
            found_ip, found_port = m.group(1), int(m.group(2))
            if target_ip and found_ip != target_ip:
                continue
            results.append({"ip": found_ip, "port": found_port, "address": f"{found_ip}:{found_port}"})
        return results

    def probe_alive(self, target: str) -> bool:
        """Deeper liveness check: a trivial shell command answers."""
        code, out, _ = self.shell(target, "echo ok", timeout=6)
        return code == 0 and "ok" in out

    def disconnect(self, target: str):
        self.run_adb(["disconnect", target], timeout=5)

    # ---------- device identity anchor ----------
    @staticmethod
    def is_valid_uuid(value: Optional[str]) -> bool:
        return bool(value and _UUID_RE.match(value.strip()))

    def read_anchor(self, target: str) -> Optional[str]:
        code, out, _ = self.shell(target, f"cat {ANCHOR_REMOTE_PATH}", timeout=8)
        if code == 0:
            val = out.strip().splitlines()[0].strip() if out.strip() else ""
            if self.is_valid_uuid(val):
                return val.lower()
        return None

    def write_anchor(self, target: str, device_uuid: str) -> bool:
        code, _, _ = self.shell(target, f"echo {device_uuid} > {ANCHOR_REMOTE_PATH}", timeout=8)
        if code != 0:
            return False
        return self.read_anchor(target) == device_uuid.lower()

    # ---------- app / package ----------
    def is_package_installed(self, target: str, package: Optional[str] = None) -> bool:
        pkg = package or settings.nusuk_package
        code, out, _ = self.shell(target, f"pm list packages {pkg}", timeout=10)
        return code == 0 and f"package:{pkg}" in out.split()

    def force_stop(self, target: str, package: Optional[str] = None):
        pkg = package or settings.nusuk_package
        self.shell(target, f"am force-stop {pkg}")

    def clear_ram(self, target: str, package: Optional[str] = None):
        """Force stop + trim RAM for the Nusuk package on this device only (single ADB round-trip)."""
        pkg = package or settings.nusuk_package
        self.shell(target, f"am force-stop {pkg}; am kill {pkg}; am send-trim-memory {pkg} COMPLETE")
        time.sleep(0.5)

    def launch_app(self, target: str, package: Optional[str] = None, activity: Optional[str] = None) -> bool:
        pkg = package or settings.nusuk_package
        act = activity or settings.nusuk_activity
        code, out, _ = self.shell(target, f"am start -n {pkg}/{act}")
        return code == 0

    def hide_keyboard(self, target: str):
        self.shell(target, "input keyevent 111")

    def reboot(self, target: str) -> bool:
        code, _, _ = self.run_adb(["-s", target, "reboot"], timeout=10)
        return code == 0

    def install_multiple(self, target: str, apk_paths: List[str], timeout: int = 180) -> Tuple[bool, str]:
        """Installs split APKs onto target via `adb -s <target> install-multiple <apks...>`."""
        missing = [p for p in apk_paths if not os.path.isfile(p)]
        if missing:
            return False, f"File APK tidak ditemukan: {', '.join(missing)}"
        rc, out, err = self.run_adb(["-s", target, "install-multiple", "-r"] + list(apk_paths), timeout=timeout)
        combined = " ".join(p for p in (out, err) if p).strip()
        ok = rc == 0 and "success" in combined.lower()
        return ok, combined or (f"exit code {rc}")

    # ---------- Redroid Auto-Provisioning ----------
    def provision_redroid_sync(
        self,
        adb_port: int,
        container_id: str,
        container_name: str,
        device_uuid: str,
        display_name: Optional[str] = None,
        adb_wait_timeout: int = 120,
    ) -> Dict[str, Any]:
        """
        Post-deployment auto-provisioning worker for a newly launched Redroid container:
          1. Wait until `adb connect 127.0.0.1:<adb_port>` succeeds and Android boot completes.
          2. Plant anchor file `/sdcard/.nusuk_device_uuid`.
          3. Install Nusuk split APKs from `Nusuk_Backup/`.
          4. Register new `tcp` instance (`127.0.0.1:<adb_port>`) in MySQL `instances` table
             and broadcast WebSocket `redroid_provisioning_event` + `device_list_update`.
        """
        from backend.database import SessionLocal
        from backend.models import Instance, LogSeverity
        from backend.event_log import log_event, set_device_name
        from backend.telemetry import telemetry_manager
        from backend.time_service import utc_now_naive
        from backend.instance_manager import instance_manager, Runtime

        target = f"127.0.0.1:{int(adb_port)}"
        inst_name = (display_name or "").strip()[:64] or f"Redroid-{adb_port}"

        def emit_progress(stage: str, status: str, message: str, **extra):
            payload = {
                "stage": stage,
                "status": status,
                "title": f"Redroid ({inst_name})",
                "message": message,
                "container_id": container_id,
                "container_name": container_name,
                "adb_port": int(adb_port),
                "adb_serial": target,
                "device_uuid": device_uuid,
                **extra,
            }
            telemetry_manager.emit_sync("redroid_provisioning_event", payload)

        try:
            # 1. Wait for ADB connection & Android boot completion
            emit_progress("waiting_adb", "progress", f"Waiting for ADB on {target}...")
            log_event(
                LogSeverity.INFO,
                "REDROID",
                f"Auto-provisioning container '{container_name}' ({target}): menunggu ADB siap...",
                None,
            )

            deadline = time.time() + max(30, int(adb_wait_timeout))
            adb_ready = False
            while time.time() < deadline:
                self.connect(target, timeout=5)
                if self.is_ready(target):
                    code_boot, out_boot, _ = self.shell(target, "getprop sys.boot_completed", timeout=5)
                    if code_boot == 0 and out_boot.strip() == "1":
                        code_pm, out_pm, _ = self.shell(target, "pm path android", timeout=5)
                        if code_pm == 0 and "package:" in out_pm:
                            adb_ready = True
                            break
                time.sleep(2.5)

            if not adb_ready:
                raise TimeoutError(
                    f"Redroid container '{container_name}' did not become ready via ADB ({target}) within {adb_wait_timeout}s."
                )

            # 2. Plant anchor file (/sdcard/.nusuk_device_uuid)
            emit_progress("planting_anchor", "progress", f"ADB connected ({target}). Planting device identity anchor...")
            anchor_ok = False
            for _ in range(10):
                if self.write_anchor(target, device_uuid):
                    anchor_ok = True
                    break
                time.sleep(1.5)
            if not anchor_ok:
                logger.warning(f"Failed to verify anchor on {target}; continuing provisioning.")

            # 3. Install Nusuk Split APKs from Nusuk_Backup/
            emit_progress("installing_apk", "progress", f"Installing Nusuk app (Split APKs) onto {inst_name}...")
            backup_dir = os.path.join(PROJECT_ROOT, "Nusuk_Backup")
            apk_paths = [os.path.join(backup_dir, fname) for fname in NUSUK_SPLIT_APKS]
            ok_install, install_msg = self.install_multiple(target, apk_paths, timeout=180)
            if not ok_install:
                raise RuntimeError(f"Gagal menginstal Nusuk APK di {target}: {install_msg}")

            nusuk_installed = self.is_package_installed(target)
            if not nusuk_installed:
                raise RuntimeError(f"Instalasi APK selesai tetapi paket {settings.nusuk_package} tidak terdeteksi.")

            # 4. Register instance in MySQL `instances` table
            emit_progress("registering_db", "progress", f"Registering {inst_name} ({target}) in database...")
            db = SessionLocal()
            try:
                existing = (
                    db.query(Instance)
                    .filter(Instance.adb_serial == target, Instance.is_deleted == False)  # noqa: E712
                    .first()
                )
                if existing:
                    existing.name = inst_name
                    existing.connection_type = "tcp"
                    existing.ip = "127.0.0.1"
                    existing.port = int(adb_port)
                    existing.nusuk_installed = True
                    existing.anchor_planted = anchor_ok
                    existing.last_seen_at = utc_now_naive()
                    row = existing
                    device_uuid = row.uuid
                else:
                    row = Instance(
                        uuid=device_uuid,
                        name=inst_name,
                        connection_type="tcp",
                        adb_serial=target,
                        ip="127.0.0.1",
                        port=int(adb_port),
                        live_preview_enabled=True,
                        nusuk_installed=True,
                        anchor_planted=anchor_ok,
                        is_legacy_primary=False,
                        last_seen_at=utc_now_naive(),
                        created_at=utc_now_naive(),
                    )
                    db.add(row)
                db.commit()
                db.refresh(row)

                set_device_name(row.uuid, row.name)
                with instance_manager._lock:
                    instance_manager.runtime[row.uuid] = Runtime(last_app_keys=row.last_app_keys)
                instance_manager.set_runtime(
                    row.uuid,
                    status="IDLE",
                    current_step="Siap digunakan",
                    offline_logged=False,
                    last_seen_at=utc_now_naive(),
                )
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

            log_event(
                LogSeverity.INFO,
                "REDROID",
                f"Redroid instance '{inst_name}' ({target}) selesai di-provision & siap digunakan.",
                device_uuid,
            )
            instance_manager.emit_device_list()
            emit_progress(
                "ready",
                "ready",
                f"Redroid instance '{inst_name}' ({target}) is Ready!",
                instance_name=inst_name,
            )
            return {
                "success": True,
                "device_uuid": device_uuid,
                "name": inst_name,
                "adb_serial": target,
                "adb_port": int(adb_port),
                "container_id": container_id,
                "container_name": container_name,
            }
        except Exception as e:
            logger.exception(f"Redroid provisioning failed for {container_name} ({target}): {e}")
            log_event(
                LogSeverity.ERROR,
                "REDROID",
                f"Auto-provisioning Redroid '{inst_name}' ({target}) gagal: {e}",
                None,
            )
            emit_progress("error", "error", f"Provisioning failed for {inst_name}: {e}")
            raise

    async def provision_redroid_instance(
        self,
        adb_port: int,
        container_id: str,
        container_name: str,
        device_uuid: str,
        display_name: Optional[str] = None,
        adb_wait_timeout: int = 120,
    ) -> Dict[str, Any]:
        """Async wrapper executing Redroid auto-provisioning without blocking the FastAPI event loop."""
        return await asyncio.to_thread(
            self.provision_redroid_sync,
            adb_port,
            container_id,
            container_name,
            device_uuid,
            display_name,
            adb_wait_timeout,
        )

    # ---------- screenshot (callers MUST check live_preview_enabled first) ----------
    def take_screenshot_bytes(self, target: str) -> Optional[bytes]:
        cmd = [self.adb_path, "-s", target, "exec-out", "screencap", "-p"]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if res.returncode == 0 and len(res.stdout) > 1000:
                raw = res.stdout
                if raw.startswith(b"\x89PNG\r\n\x1a\n"):
                    return raw
                if raw.startswith(b"\x89PNG\r\r\n"):
                    return raw.replace(b"\r\r\n", b"\r\n")
                return raw
        except Exception as e:
            logger.debug(f"Screenshot capture failed for {target}: {e}")
        return None


adb_manager = ADBManager()
