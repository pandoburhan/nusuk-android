#!/usr/bin/env python3
"""
Nusuk Quiz & Key Automation Hub (v2.0) — Cross-Platform System Runner
Supports Windows and Linux (Desktop & Headless VPS).
"""

import argparse
import os
import platform
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import webbrowser

IS_WINDOWS = os.name == "nt" or platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"


def get_lan_ip() -> str:
    """Detects local LAN / outbound IPv4 address of this machine."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        try:
            s.connect(("192.168.1.1", 80))
            ip = s.getsockname()[0]
        except Exception:
            ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def parse_args():
    parser = argparse.ArgumentParser(description="Nusuk Quiz & Key Automation Hub Runner")
    parser.add_argument(
        "-m",
        "--mode",
        choices=["local", "lan"],
        help="Access mode: 'local' (127.0.0.1 only) or 'lan' (accessible across LAN/VPS on 0.0.0.0)",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Force Vite development server (port 5173) + uvicorn --reload instead of native built frontend",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Backend FastAPI port (default: 8000)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not attempt to open the browser automatically",
    )
    return parser.parse_args()


def choose_mode_interactively() -> str:
    print("-" * 65)
    print(" PILIH MODE AKSES JARINGAN DASHBOARD:")
    print(" [1] Local Only (Hanya mesin ini - 127.0.0.1)")
    print(" [2] LAN / VPS Network (Dapat diakses dari perangkat lain - 0.0.0.0)")
    print("-" * 65)
    try:
        choice = input("Pilihan Anda [1/2] (Default: 1): ").strip()
    except (EOFError, KeyboardInterrupt):
        choice = "1"

    if choice == "2":
        return "lan"
    return "local"


def verify_and_prepare_adb(root_dir: str) -> str:
    adb_name = "adb.exe" if IS_WINDOWS else "adb"
    adb_path = os.path.join(root_dir, "platform-tools", adb_name)

    if not os.path.exists(adb_path):
        print(f"[-] PERINGATAN: Binary ADB tidak ditemukan di {adb_path}")
        print("    Jalankan 'python install.py' untuk mengunduh platform-tools secara otomatis.")
        return adb_path

    if not IS_WINDOWS:
        try:
            cur_mode = os.stat(adb_path).st_mode
            if not os.access(adb_path, os.X_OK):
                os.chmod(adb_path, cur_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                print(f"[+] Permission eksekusi (chmod +x) diterapkan ke: {adb_path}")
        except OSError as e:
            print(f"[!] Gagal menerapkan chmod +x pada {adb_path}: {e}")

    print(f"[+] Binary ADB lokal terverifikasi ({platform.system()}): {adb_path}")
    return adb_path


def has_gui_display() -> bool:
    if IS_WINDOWS or platform.system() == "Darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def resolve_python_executable(root_dir: str) -> str:
    """Prefer project-local .venv Python if present so users can run `python3 run_system.py` directly."""
    candidates = (
        [os.path.join(root_dir, ".venv", "Scripts", "python.exe")]
        if IS_WINDOWS
        else [os.path.join(root_dir, ".venv", "bin", "python"), os.path.join(root_dir, ".venv", "bin", "python3")]
    )
    for cand in candidates:
        if os.path.isfile(cand):
            return cand
    return sys.executable


def terminate_process(proc: subprocess.Popen, name: str) -> None:
    if not proc or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
    except Exception as e:
        print(f"[!] Peringatan saat menghentikan {name}: {e}")


def main():
    args = parse_args()
    mode = args.mode

    print("=" * 65)
    print("   NUSUK QUIZ & KEY AUTOMATION HUB — SYSTEM RUNNER (v2.0)")
    print(f"   OS: {platform.system()} {platform.release()} ({platform.machine()})")
    print("=" * 65)

    if not mode:
        mode = choose_mode_interactively()

    root_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(root_dir)

    # 1. Check .env
    if not os.path.exists(".env"):
        print("[!] File .env tidak ditemukan. Menyalin dari .env.example...")
        if os.path.exists(".env.example"):
            shutil.copy(".env.example", ".env")
            print("[+] .env berhasil dibuat. Disarankan menjalankan 'python install.py' untuk konfigurasi penuh.")

    # 2. Check & prepare local ADB binary (cross-platform)
    verify_and_prepare_adb(root_dir)

    # 3. Determine Host bindings
    lan_ip = get_lan_ip()
    if mode == "lan":
        bind_host = "0.0.0.0"
        mode_label = "LAN / VPS Network Mode (0.0.0.0)"
    else:
        bind_host = "127.0.0.1"
        mode_label = "Local Only Mode (127.0.0.1)"

    frontend_dir = os.path.join(root_dir, "frontend")
    frontend_dist_index = os.path.join(frontend_dir, "dist", "index.html")
    use_vite_dev = args.dev or not os.path.exists(frontend_dist_index)

    print(f"\n[*] Mode Aktif     : {mode_label}")
    print(f"[*] Mode Frontend  : {'Vite Dev Server (Port 5173)' if use_vite_dev else 'Native Built SPA via FastAPI (Port ' + str(args.port) + ')'}")

    # 4. Start Backend FastAPI
    backend_python = resolve_python_executable(root_dir)
    print(f"[*] Menjalankan Backend FastAPI (Host: {bind_host}, Port: {args.port})...")
    if backend_python != sys.executable:
        print(f"[+] Menggunakan Python Virtual Environment: {backend_python}")
    backend_cmd = [
        backend_python,
        "-m",
        "uvicorn",
        "backend.main:app",
        "--host",
        bind_host,
        "--port",
        str(args.port),
    ]
    if use_vite_dev:
        backend_cmd.append("--reload")

    backend_proc = subprocess.Popen(backend_cmd, cwd=root_dir)
    frontend_proc = None

    # 5. Start Vite Dev Server only if --dev or frontend/dist is missing
    if use_vite_dev:
        print(f"[*] Menjalankan Frontend Vite React (Host: {bind_host}, Port: 5173)...")
        if IS_WINDOWS:
            frontend_cmd = f"npm run dev -- --host {bind_host} --port 5173"
            frontend_proc = subprocess.Popen(frontend_cmd, cwd=frontend_dir, shell=True)
        else:
            npm_bin = shutil.which("npm") or "npm"
            frontend_cmd = [npm_bin, "run", "dev", "--", "--host", bind_host, "--port", "5173"]
            frontend_proc = subprocess.Popen(frontend_cmd, cwd=frontend_dir)

    time.sleep(2.5)

    dashboard_port = 5173 if use_vite_dev else args.port
    local_url = f"http://localhost:{dashboard_port}"

    print("\n" + "=" * 65)
    print("   SISTEM TELAH SIAP DAN SEDANG BERJALAN!")
    print("=" * 65)
    print(f" [+] Dashboard Lokal             : {local_url}")
    if mode == "lan":
        print(f" [+] Dashboard LAN / VPS         : http://{lan_ip}:{dashboard_port}")
        print(f" [+] Backend API & Swagger Docs  : http://{lan_ip}:{args.port}/docs")
        if IS_WINDOWS:
            print(f" [i] Catatan Firewall (Windows)  : Pastikan port {dashboard_port} (dan {args.port}) diizinkan di Windows Firewall.")
        else:
            print(f" [i] Catatan Firewall (Linux)    : Pastikan port {dashboard_port} dibuka (misal: sudo ufw allow {dashboard_port}/tcp).")
    else:
        print(f" [+] Backend API & Swagger Docs  : http://127.0.0.1:{args.port}/docs")
    print("=" * 65)
    print(" Tekan Ctrl+C untuk menghentikan seluruh sistem dengan aman.\n")

    if not args.no_browser and has_gui_display():
        try:
            webbrowser.open(local_url)
        except Exception:
            pass

    stopping = False

    def _handle_signal(signum, frame):
        nonlocal stopping
        stopping = True

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    try:
        while not stopping:
            if backend_proc.poll() is not None:
                print(f"\n[!] Backend process berhenti dengan kode keluar {backend_proc.returncode}.")
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[*] Menghentikan server backend" + (" dan frontend..." if frontend_proc else "..."))
        terminate_process(frontend_proc, "Frontend Vite")
        terminate_process(backend_proc, "Backend FastAPI")
        print("[+] Seluruh proses telah dihentikan. Sampai jumpa!")


if __name__ == "__main__":
    main()
