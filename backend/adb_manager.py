import os
import re
import subprocess
import time
import logging
from typing import Optional, Tuple, Dict, List, Any
from backend.config import settings

logger = logging.getLogger(__name__)

ANCHOR_REMOTE_PATH = "/sdcard/.nusuk_device_uuid"
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
        self.adb_path = os.path.abspath(adb_path or settings.adb_path)
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
