#!/usr/bin/env python3
"""
Interactive UI Hierarchy (XML) & Screenshot Dumper for Nusuk Android Login Flow.

Guides the QA engineer step-by-step through all login phases (Fresh Install Welcome
Slides -> Main Login -> Credentials -> OTP Verification -> Dashboard -> Session Timeout)
and saves both `.xml` and `.png` dumps into `docs/login_flow_dumps/`.

Usage:
    python tools/login_dumper.py
    python tools/login_dumper.py --serial 192.168.100.8:21504
    python tools/login_dumper.py --from 9
    python tools/login_dumper.py --only 9 11
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Paths & ADB Environment Bootstrap (must run BEFORE importing uiautomator2)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLATFORM_TOOLS_DIR = PROJECT_ROOT / "platform-tools"
OUTPUT_DIR = PROJECT_ROOT / "docs" / "login_flow_dumps"
DEFAULT_ADB_TARGET = "192.168.100.8:21504"


def resolve_adb_path() -> str:
    """Resolve ADB binary from project's platform-tools folder first, else PATH."""
    exe_name = "adb.exe" if os.name == "nt" else "adb"
    local_adb = PLATFORM_TOOLS_DIR / exe_name
    if local_adb.is_file():
        return str(local_adb)
    return exe_name


ADB_PATH = resolve_adb_path()
os.environ["ADBUTILS_ADB_PATH"] = ADB_PATH
if PLATFORM_TOOLS_DIR.is_dir():
    os.environ["PATH"] = str(PLATFORM_TOOLS_DIR) + os.pathsep + os.environ.get("PATH", "")


# ---------------------------------------------------------------------------
# Terminal Colors (Windows VT100 + POSIX ANSI, zero external dependencies)
# ---------------------------------------------------------------------------
def _enable_windows_ansi() -> None:
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        except Exception:
            os.system("")


_enable_windows_ansi()


class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"


def log_info(msg: str) -> None:
    print(f"{Color.CYAN}[INFO]{Color.RESET} {msg}")


def log_ok(msg: str) -> None:
    print(f"{Color.GREEN}[OK]{Color.RESET}   {msg}")


def log_warn(msg: str) -> None:
    print(f"{Color.YELLOW}[WARN]{Color.RESET} {msg}")


def log_err(msg: str) -> None:
    print(f"{Color.RED}[ERR]{Color.RESET}  {msg}")


def print_banner() -> None:
    line = "=" * 72
    print(f"\n{Color.BOLD}{Color.BLUE}{line}{Color.RESET}")
    print(
        f"{Color.BOLD}{Color.WHITE}"
        "   NUSUK ANDROID — INTERACTIVE LOGIN FLOW XML & SCREENSHOT DUMPER"
        f"{Color.RESET}"
    )
    print(f"{Color.BOLD}{Color.BLUE}{line}{Color.RESET}")
    print(f"{Color.DIM}ADB Binary : {ADB_PATH}{Color.RESET}")
    print(f"{Color.DIM}Output Dir : {OUTPUT_DIR}{Color.RESET}\n")


# ---------------------------------------------------------------------------
# Dump Step Definitions (Blueprint Bagian 1)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DumpStep:
    index: int
    phase: str
    filename: str
    instruction: str
    urgent: bool = False


DUMP_STEPS: List[DumpStep] = [
    # Fase 1: Welcome Screen (Fresh Install)
    DumpStep(
        index=1,
        phase="Fase 1: Welcome Screen (Fresh Install)",
        filename="01_welcome_slide_1",
        instruction=(
            "Buka aplikasi Nusuk yang baru diinstall. "
            "Pastikan berada di Welcome Slide 1 ('Umrah in Minutes'). Tekan ENTER untuk dump."
        ),
    ),
    DumpStep(
        index=2,
        phase="Fase 1: Welcome Screen (Fresh Install)",
        filename="02_welcome_slide_2",
        instruction="Klik panah kanan ke Slide 2 ('Enter the Prophet's...'). Tekan ENTER.",
    ),
    DumpStep(
        index=3,
        phase="Fase 1: Welcome Screen (Fresh Install)",
        filename="03_welcome_slide_3",
        instruction="Klik panah kanan ke Slide 3. Tekan ENTER.",
    ),
    DumpStep(
        index=4,
        phase="Fase 1: Welcome Screen (Fresh Install)",
        filename="04_welcome_slide_4",
        instruction="Klik panah kanan ke Slide 4. Tekan ENTER.",
    ),
    DumpStep(
        index=5,
        phase="Fase 1: Welcome Screen (Fresh Install)",
        filename="05_welcome_slide_5",
        instruction="Klik panah kanan ke Slide 5 ('Manage your payments...'). Tekan ENTER.",
    ),
    # Fase 2: Login Screen Utama
    DumpStep(
        index=6,
        phase="Fase 2: Login Screen Utama",
        filename="06_login_main",
        instruction="Pastikan berada di layar 'Are you ready to start...'. Tekan ENTER.",
    ),
    DumpStep(
        index=7,
        phase="Fase 2: Login Screen Utama",
        filename="07_login_method_popup",
        instruction=(
            "Klik 'Login Or Register'. Muncul pop-up 'Log In to Continue' "
            "(Email vs Nafath). Tekan ENTER."
        ),
    ),
    # Fase 3: Kredensial (Email & Password)
    DumpStep(
        index=8,
        phase="Fase 3: Kredensial (Email & Password)",
        filename="08_credentials_form",
        instruction="Klik 'Login with Email'. Pastikan berada di form email & password. Tekan ENTER.",
    ),
    DumpStep(
        index=9,
        phase="Fase 3: Kredensial (Email & Password)",
        filename="09_credentials_error",
        instruction=(
            "Masukkan email yang salah / password yang salah. Klik Login. "
            "Biarkan error / toast muncul. Cepat tekan ENTER."
        ),
        urgent=True,
    ),
    DumpStep(
        index=10,
        phase="Fase 3: Kredensial (Email & Password)",
        filename="10_verify_otp_form",
        instruction=(
            "Masukkan email dan password yang BENAR. "
            "(Pastikan Remember me dimatikan secara manual). "
            "Klik Login dan tunggu layar pindah ke Verify Email. Tekan ENTER."
        ),
    ),
    # Fase 4: Verifikasi (OTP 4 Digit)
    DumpStep(
        index=11,
        phase="Fase 4: Verifikasi (OTP 4 Digit)",
        filename="11_verify_otp_error",
        instruction="Masukkan OTP yang SALAH. Klik Verify. Saat error muncul, tekan ENTER.",
        urgent=True,
    ),
    DumpStep(
        index=12,
        phase="Fase 4: Verifikasi (OTP 4 Digit)",
        filename="12_dashboard_success",
        instruction="Masukkan OTP yang BENAR. Masuk ke Dashboard Utama. Tekan ENTER.",
    ),
    # Fase 5: Session Timeout (Logged Out)
    DumpStep(
        index=13,
        phase="Fase 5: Session Timeout (Logged Out)",
        filename="13_session_timed_out",
        instruction=(
            "Sekarang, loginkan akun tersebut dari HP/perangkat lain agar sesi di emulator ini mati, "
            "ATAU lakukan logout. Pastikan layar menampilkan 'Session Timed Out' dan opsi "
            "'Login with Email'. Tekan ENTER."
        ),
    ),
]


# ---------------------------------------------------------------------------
# ADB Helpers
# ---------------------------------------------------------------------------
_TCP_SERIAL_RE = re.compile(r"^[^:\s]+:\d{1,5}$")


def run_adb(args: List[str], timeout: int = 15) -> Tuple[int, str, str]:
    cmd = [ADB_PATH] + args
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except FileNotFoundError:
        return -1, "", f"ADB binary tidak ditemukan di: {ADB_PATH}"
    except subprocess.TimeoutExpired:
        return -1, "", "Perintah ADB timeout."
    except Exception as exc:
        return -1, "", str(exc)


def list_online_devices() -> List[str]:
    code, out, err = run_adb(["devices"])
    if code != 0:
        log_warn(f"Gagal menjalankan 'adb devices': {err or out}")
        return []
    devices: List[str] = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or "\t" not in line:
            continue
        serial, state = line.split("\t", 1)
        if state.strip() == "device":
            devices.append(serial.strip())
    return devices


def ensure_adb_connected(target: str) -> bool:
    """Connect via TCP if target is host:port, then verify device is online."""
    target = target.strip()
    if _TCP_SERIAL_RE.match(target):
        log_info(f"Menjalankan 'adb connect {target}'...")
        code, out, err = run_adb(["connect", target], timeout=10)
        combined = f"{out} {err}".strip()
        if code != 0 or ("failed" in combined.lower() or "unable to connect" in combined.lower()):
            log_err(f"ADB connect gagal: {combined}")
            return False
        log_ok(f"ADB response: {out or err}")

    online = list_online_devices()
    if target in online:
        return True

    log_err(f"Perangkat '{target}' tidak ditemukan dalam status 'device'. Online saat ini: {online or '-'}")
    return False


def select_target_device(cli_serial: Optional[str]) -> str:
    """Determine target ADB serial via CLI arg, auto-detection, or user prompt."""
    if cli_serial:
        return cli_serial.strip()

    online = list_online_devices()
    if online:
        log_info(f"Perangkat ADB terdeteksi online: {', '.join(online)}")
    else:
        log_warn("Belum ada perangkat ADB online yang terdeteksi.")

    if len(online) == 1:
        suggested = online[0]
        prompt_hint = f"Terdeteksi 1 perangkat: {suggested}"
    else:
        suggested = DEFAULT_ADB_TARGET
        prompt_hint = f"Default target: {suggested}"

    print(
        f"{Color.YELLOW}Masukkan ADB Serial / IP:Port{Color.RESET} "
        f"{Color.DIM}({prompt_hint}; tekan ENTER untuk pakai default){Color.RESET}"
    )
    user_val = input(f"{Color.BOLD}> ADB Target [{suggested}]: {Color.RESET}").strip()
    return user_val or suggested


# ---------------------------------------------------------------------------
# UIAutomator2 Connection & Dumping
# ---------------------------------------------------------------------------
def connect_u2(serial: str):
    try:
        import uiautomator2 as u2
    except ImportError as exc:
        log_err(
            "Library 'uiautomator2' belum terinstall. "
            "Jalankan: pip install uiautomator2"
        )
        raise SystemExit(1) from exc

    log_info(f"Menghubungkan uiautomator2 ke {serial}...")
    d = u2.connect(serial)

    # Verifikasi koneksi aktif dengan mengambil info dasar perangkat
    info = d.info
    width = info.get("displayWidth", "?")
    height = info.get("displayHeight", "?")
    product = info.get("productName", "unknown")
    current_app = {}
    try:
        current_app = d.app_current()
    except Exception:
        pass

    pkg = current_app.get("package", "-")
    act = current_app.get("activity", "-")
    log_ok(f"Terhubung ke {serial} | Device: {product} ({width}x{height}) | Foreground: {pkg} ({act})")
    return d


def dump_screen(d, output_dir: Path, base_name: str) -> Tuple[Path, Path, float]:
    """Capture UI XML hierarchy and PNG screenshot immediately."""
    start = time.perf_counter()
    xml_path = output_dir / f"{base_name}.xml"
    png_path = output_dir / f"{base_name}.png"

    # Dump XML hierarchy terlebih dahulu (paling krusial untuk toast/error singkat)
    xml_content = d.dump_hierarchy(compressed=False, pretty=True)
    xml_path.write_text(xml_content, encoding="utf-8")

    # Ambil screenshot layar
    d.screenshot(str(png_path))

    elapsed = time.perf_counter() - start
    return xml_path, png_path, elapsed


# ---------------------------------------------------------------------------
# Main Interactive Flow
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactive XML & Screenshot Dumper untuk Audit Layar Login Nusuk."
    )
    parser.add_argument(
        "-s",
        "--serial",
        type=str,
        default=None,
        help=f"ADB serial atau IP:Port (contoh: {DEFAULT_ADB_TARGET})",
    )
    parser.add_argument(
        "--from",
        dest="start_from",
        type=int,
        default=1,
        help="Mulai dari nomor langkah tertentu (1-13). Default: 1.",
    )
    parser.add_argument(
        "--only",
        type=int,
        nargs="+",
        default=None,
        help="Jalankan hanya nomor langkah tertentu (contoh: --only 9 11).",
    )
    return parser.parse_args()


def filter_steps(args: argparse.Namespace) -> List[DumpStep]:
    if args.only:
        allowed = set(args.only)
        steps = [s for s in DUMP_STEPS if s.index in allowed]
    else:
        steps = [s for s in DUMP_STEPS if s.index >= args.start_from]

    if not steps:
        log_err("Tidak ada langkah yang cocok dengan filter --from / --only (valid: 1..13).")
        raise SystemExit(1)
    return steps


def print_summary(results: Dict[int, str], steps: List[DumpStep]) -> None:
    line = "=" * 72
    print(f"\n{Color.BOLD}{Color.BLUE}{line}{Color.RESET}")
    print(f"{Color.BOLD}{Color.WHITE}RINGKASAN HASIL DUMP{Color.RESET}")
    print(f"{Color.BOLD}{Color.BLUE}{line}{Color.RESET}")

    ok_count = 0
    skip_count = 0
    fail_count = 0

    for step in steps:
        status = results.get(step.index, "NOT_RUN")
        if status == "OK":
            ok_count += 1
            badge = f"{Color.GREEN}OK     {Color.RESET}"
        elif status == "SKIPPED":
            skip_count += 1
            badge = f"{Color.YELLOW}SKIPPED{Color.RESET}"
        elif status == "FAILED":
            fail_count += 1
            badge = f"{Color.RED}FAILED {Color.RESET}"
        else:
            badge = f"{Color.DIM}NOT_RUN{Color.RESET}"
        print(f"  [{step.index:02d}] {badge}  {step.filename}.xml / .png")

    print(f"{Color.BLUE}{'-' * 72}{Color.RESET}")
    print(
        f"  Berhasil: {Color.GREEN}{ok_count}{Color.RESET} | "
        f"Dilewati: {Color.YELLOW}{skip_count}{Color.RESET} | "
        f"Gagal: {Color.RED}{fail_count}{Color.RESET}"
    )
    print(f"  Folder Output: {Color.CYAN}{OUTPUT_DIR}{Color.RESET}")
    if fail_count or skip_count:
        print(
            f"  {Color.DIM}Tip: Untuk mengulang langkah tertentu, jalankan: "
            f"python tools/login_dumper.py --only <nomor>{Color.RESET}"
        )
    print(f"{Color.BOLD}{Color.BLUE}{line}{Color.RESET}\n")


def main() -> int:
    print_banner()
    args = parse_args()
    steps = filter_steps(args)

    # 1. Pastikan folder output tersedia
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        log_ok(f"Folder output siap: {OUTPUT_DIR}")
    except Exception as exc:
        log_err(f"Gagal membuat folder output '{OUTPUT_DIR}': {exc}")
        return 1

    # 2. Pilih & sambungkan ADB + UIAutomator2
    try:
        serial = select_target_device(args.serial)
        if not ensure_adb_connected(serial):
            log_err("Koneksi ADB gagal. Pastikan emulator/HP menyala dan IP:Port benar.")
            return 1
        d = connect_u2(serial)
    except KeyboardInterrupt:
        print()
        log_warn("Dibatalkan oleh user sebelum proses dump dimulai.")
        return 130
    except SystemExit:
        raise
    except Exception as exc:
        log_err(f"Gagal inisialisasi koneksi uiautomator2: {exc}")
        return 1

    # 3. Jalankan alur interaktif sekuensial
    print(
        f"\n{Color.DIM}Kontrol di setiap langkah: "
        f"[ENTER] = Dump | [s] = Skip langkah | [q] = Keluar{Color.RESET}"
    )

    results: Dict[int, str] = {}
    current_phase: Optional[str] = None
    total_steps = len(DUMP_STEPS)

    try:
        for step in steps:
            if step.phase != current_phase:
                current_phase = step.phase
                print(f"\n{Color.BOLD}{Color.MAGENTA}>>> {current_phase} <<<{Color.RESET}")

            print(
                f"\n{Color.BOLD}{Color.CYAN}[Langkah {step.index:02d}/{total_steps:02d}]{Color.RESET} "
                f"{Color.DIM}Target file: {step.filename}.xml & .png{Color.RESET}"
            )
            if step.urgent:
                print(
                    f"{Color.YELLOW}{Color.BOLD}⚡ TIP:{Color.RESET} "
                    f"{Color.YELLOW}Pesan error/toast bisa hilang cepat — siapkan jari di tombol ENTER!{Color.RESET}"
                )
            print(f"  {Color.WHITE}{step.instruction}{Color.RESET}")

            while True:
                choice = input(
                    f"{Color.BOLD}{Color.GREEN}  >> Tekan ENTER untuk dump "
                    f"(atau ketik 's' skip / 'q' keluar): {Color.RESET}"
                ).strip().lower()

                if choice == "q":
                    log_warn("Proses dihentikan lebih awal oleh user.")
                    print_summary(results, steps)
                    return 0

                if choice == "s":
                    log_warn(f"Langkah {step.index:02d} ({step.filename}) dilewati.")
                    results[step.index] = "SKIPPED"
                    break

                if choice not in ("", "r"):
                    log_warn("Input tidak dikenal. Tekan ENTER untuk dump, 's' untuk skip, atau 'q' untuk keluar.")
                    continue

                # Lakukan dump XML + Screenshot
                try:
                    xml_path, png_path, elapsed = dump_screen(d, OUTPUT_DIR, step.filename)
                    xml_kb = xml_path.stat().st_size / 1024.0
                    png_kb = png_path.stat().st_size / 1024.0
                    log_ok(
                        f"Tersimpan dalam {elapsed:.2f}s -> "
                        f"{xml_path.name} ({xml_kb:.1f} KB) & {png_path.name} ({png_kb:.1f} KB)"
                    )
                    results[step.index] = "OK"
                    break
                except Exception as exc:
                    log_err(f"Gagal melakukan dump pada langkah {step.index:02d}: {exc}")
                    retry = input(
                        f"{Color.YELLOW}  Coba ulang langkah ini? [ENTER/r = Ulang | s = Skip | q = Keluar]: {Color.RESET}"
                    ).strip().lower()
                    if retry == "q":
                        results[step.index] = "FAILED"
                        print_summary(results, steps)
                        return 1
                    if retry == "s":
                        results[step.index] = "FAILED"
                        break
                    # Else: loop dan coba dump ulang
                    log_info("Mengulangi proses dump...")

    except KeyboardInterrupt:
        print()
        log_warn("Interupsi (Ctrl+C) diterima. Menyimpan ringkasan progres sejauh ini...")
        print_summary(results, steps)
        return 130

    print_summary(results, steps)
    log_ok("Semua langkah selesai! File hasil dump siap dianalisis untuk Blueprint Bagian 2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
