import os
import sys
import time
import socket
import argparse
import subprocess
import webbrowser

def get_lan_ip():
    """Detects local LAN IPv4 address of this machine."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connect to local gateway or target emulator to determine outbound interface IP
        s.connect(('192.168.100.8', 5555))
        ip = s.getsockname()[0]
    except Exception:
        try:
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
        except Exception:
            ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def parse_args():
    parser = argparse.ArgumentParser(description="Nusuk Quiz & Key Automation Hub Runner")
    parser.add_argument(
        "-m", "--mode",
        choices=["local", "lan"],
        help="Access mode: 'local' (127.0.0.1 only) or 'lan' (accessible across LAN on 0.0.0.0)"
    )
    return parser.parse_args()

def choose_mode_interactively():
    print("-" * 65)
    print(" PILIH MODE AKSES JARINGAN DASHBOARD:")
    print(" [1] Local Only (Hanya PC ini - 127.0.0.1)")
    print(" [2] LAN / Network (Dapat diakses dari PC lain di jaringan yang sama - 0.0.0.0)")
    print("-" * 65)
    try:
        choice = input("Pilihan Anda [1/2] (Default: 1): ").strip()
    except (EOFError, KeyboardInterrupt):
        choice = "1"

    if choice == "2":
        return "lan"
    return "local"

def main():
    args = parse_args()
    mode = args.mode

    print("=" * 65)
    print("   NUSUK QUIZ & KEY AUTOMATION HUB — SYSTEM RUNNER")
    print("=" * 65)

    if not mode:
        mode = choose_mode_interactively()

    root_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(root_dir)

    # 1. Check .env
    if not os.path.exists(".env"):
        print("[!] File .env tidak ditemukan. Menyalin dari .env.example...")
        if os.path.exists(".env.example"):
            import shutil
            shutil.copy(".env.example", ".env")
            print("[+] .env berhasil dibuat.")

    # 2. Check local ADB
    adb_path = os.path.join(root_dir, "platform-tools", "adb.exe")
    if not os.path.exists(adb_path):
        print(f"[-] PERINGATAN: Binary ADB tidak ditemukan di {adb_path}")
    else:
        print(f"[+] Binary ADB lokal terverifikasi: {adb_path}")

    # Determine Host bindings
    lan_ip = get_lan_ip()
    if mode == "lan":
        backend_host = "0.0.0.0"
        frontend_host = "0.0.0.0"
        mode_label = "LAN / Network Mode (Multi-Device Access)"
    else:
        backend_host = "127.0.0.1"
        frontend_host = "127.0.0.1"
        mode_label = "Local Only Mode (This PC Only)"

    print(f"\n[*] Mode Aktif: {mode_label}")

    # 3. Start Backend API
    print(f"[*] Menjalankan Backend FastAPI (Host: {backend_host}, Port: 8000)...")
    backend_cmd = [
        sys.executable, "-m", "uvicorn", "backend.main:app",
        "--host", backend_host,
        "--port", "8000",
        "--reload"
    ]
    backend_proc = subprocess.Popen(backend_cmd, cwd=root_dir)

    # 4. Start Frontend
    frontend_dir = os.path.join(root_dir, "frontend")
    print(f"[*] Menjalankan Frontend Vite React (Host: {frontend_host}, Port: 5173)...")
    npm_bin = "npm.cmd" if os.name == "nt" else "npm"
    frontend_cmd = [npm_bin, "run", "dev", "--", "--host", frontend_host, "--port", "5173"]
    frontend_proc = subprocess.Popen(frontend_cmd, cwd=frontend_dir)

    time.sleep(3)

    print("\n" + "=" * 65)
    print("   SISTEM TELAH SIAP DAN SEDANG BERJALAN!")
    print("=" * 65)
    print(f" [+] Akses Lokal (PC B)          : http://localhost:5173")
    if mode == "lan":
        print(f" [+] Akses dari PC Lain (LAN)    : http://{lan_ip}:5173")
        print(f" [+] Backend API & Swagger Docs  : http://{lan_ip}:8000/docs")
        print(f" [i] Catatan Firewall: Jika tidak dapat diakses dari PC lain, pastikan")
        print(f"     port 5173 dan 8000 telah diizinkan pada Windows Defender Firewall.")
    else:
        print(f" [+] Backend API & Swagger Docs  : http://127.0.0.1:8000/docs")
    print("=" * 65)
    print(" Tekan Ctrl+C untuk menghentikan seluruh sistem dengan aman.\n")

    try:
        webbrowser.open("http://localhost:5173")
    except Exception:
        pass

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] Menghentikan server backend dan frontend...")
        frontend_proc.terminate()
        backend_proc.terminate()
        print("[+] Seluruh proses telah dihentikan. Sampai jumpa!")

if __name__ == "__main__":
    main()
