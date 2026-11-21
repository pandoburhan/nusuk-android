#!/usr/bin/env python3
"""
Nusuk Automation Hub (v2.0) — Universal Cross-Platform Installer
Supports Windows and Linux (Ubuntu/Debian).

Workflow:
  1. Host & OS specification detection (OS, CPU, RAM, Disk)
  2. OS dependency checks & auto-installation (Python >= 3.10, pip, Node.js/npm, Docker on Linux)
  3. Android Platform-Tools (ADB) download & extraction into ./platform-tools
  4. Interactive MySQL database & .env configuration
  5. Python backend dependencies + React frontend npm install & production build
  6. Finalization & startup instructions
"""

import ctypes
import getpass
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent
PLATFORM_TOOLS_DIR = ROOT_DIR / "platform-tools"
FRONTEND_DIR = ROOT_DIR / "frontend"
REQUIREMENTS_FILE = ROOT_DIR / "requirements.txt"
BACKEND_REQUIREMENTS_FILE = ROOT_DIR / "backend" / "requirements.txt"
ENV_FILE = ROOT_DIR / ".env"
ENV_EXAMPLE_FILE = ROOT_DIR / ".env.example"

ADB_URLS = {
    "Windows": "https://dl.google.com/android/repository/platform-tools-latest-windows.zip",
    "Linux": "https://dl.google.com/android/repository/platform-tools-latest-linux.zip",
    "Darwin": "https://dl.google.com/android/repository/platform-tools-latest-darwin.zip",
}

IS_WINDOWS = os.name == "nt" or platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"


# ---------------------------------------------------------------------------
# UI / Formatting Helpers
# ---------------------------------------------------------------------------

def banner(title: str) -> None:
    line = "=" * 70
    print(f"\n{line}")
    print(f"  {title}")
    print(line)


def step_header(step_num: int, total: int, title: str) -> None:
    print(f"\n[{step_num}/{total}] {title}")
    print("-" * 70)


def info(msg: str) -> None:
    print(f"  [*] {msg}")


def ok(msg: str) -> None:
    print(f"  [+] {msg}")


def warn(msg: str) -> None:
    print(f"  [!] {msg}")


def err(msg: str) -> None:
    print(f"  [X] {msg}")


def prompt_input(label: str, default: str = "") -> str:
    suffix = f" [Default: {default}]" if default != "" else " [Default: kosong]"
    val = input(f"  > {label}{suffix}: ").strip()
    return val if val != "" else default


def prompt_yes_no(question: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    ans = input(f"  > {question} [{hint}]: ").strip().lower()
    if not ans:
        return default
    return ans in ("y", "yes", "ya")


def run_cmd(
    cmd,
    cwd: Optional[Path] = None,
    shell: bool = False,
    capture: bool = False,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """Cross-platform subprocess wrapper with clean error propagation."""
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        shell=shell,
        check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
    )


# ---------------------------------------------------------------------------
# Step 1: Host & OS Detection
# ---------------------------------------------------------------------------

def get_linux_distro() -> str:
    os_release = Path("/etc/os-release")
    if not os_release.exists():
        return "Linux"
    try:
        for line in os_release.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return "Linux"


def get_total_ram_gb() -> Optional[float]:
    try:
        if IS_WINDOWS:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat_mem = MEMORYSTATUSEX()
            stat_mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat_mem)):
                return stat_mem.ullTotalPhys / (1024 ** 3)
        elif IS_LINUX:
            meminfo = Path("/proc/meminfo")
            if meminfo.exists():
                for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return kb / (1024 ** 2)
        else:
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip()
            return int(out) / (1024 ** 3)
    except Exception:
        return None
    return None


def detect_host_specs() -> Dict[str, object]:
    step_header(1, 6, "Deteksi Host & Spesifikasi Sistem")
    sys_name = platform.system()
    os_detail = get_linux_distro() if IS_LINUX else f"{sys_name} {platform.release()}"
    arch = platform.machine() or platform.architecture()[0]
    cpu_cores = os.cpu_count() or 1
    ram_gb = get_total_ram_gb()
    disk = shutil.disk_usage(str(ROOT_DIR))
    disk_total_gb = disk.total / (1024 ** 3)
    disk_free_gb = disk.free / (1024 ** 3)

    info(f"Sistem Operasi : {os_detail} ({sys_name})")
    info(f"Arsitektur CPU : {arch} ({cpu_cores} core)")
    info(f"Total RAM      : {f'{ram_gb:.2f} GB' if ram_gb is not None else 'Tidak terdeteksi'}")
    info(f"Disk Storage   : {disk_free_gb:.2f} GB bebas / {disk_total_gb:.2f} GB total")
    info(f"Direktori Root : {ROOT_DIR}")

    if disk_free_gb < 2.0:
        warn("Ruang disk bebas kurang dari 2 GB. Instalasi/build mungkin memerlukan ruang lebih.")

    return {
        "os": sys_name,
        "os_detail": os_detail,
        "arch": arch,
        "cpu_cores": cpu_cores,
        "ram_gb": ram_gb,
        "disk_total_gb": disk_total_gb,
        "disk_free_gb": disk_free_gb,
    }


# ---------------------------------------------------------------------------
# Step 2: OS Dependency Check & Auto-Install
# ---------------------------------------------------------------------------

def check_python_and_pip() -> None:
    major, minor = sys.version_info[:2]
    info(f"Memeriksa Python... terdeteksi Python {major}.{minor}.{sys.version_info.micro}")
    if (major, minor) < (3, 10):
        err(f"Python minimal versi 3.10 diperlukan (saat ini: {major}.{minor}).")
        if IS_WINDOWS:
            print("  -> Download Python 3.10+ di: https://www.python.org/downloads/windows/")
            print("  -> Pastikan mencentang 'Add Python to PATH' saat instalasi.")
        elif IS_LINUX:
            print("  -> Jalankan: sudo apt update && sudo apt install -y python3 python3-pip python3-venv")
        sys.exit(1)
    ok(f"Python {major}.{minor} memenuhi syarat (>= 3.10).")

    try:
        res = run_cmd([sys.executable, "-m", "pip", "--version"], capture=True)
        if res.returncode != 0:
            raise RuntimeError(res.stderr or "pip tidak ditemukan")
        ok(f"pip terdeteksi: {res.stdout.strip()}")
    except Exception as e:
        err(f"pip tidak ditemukan atau bermasalah: {e}")
        if IS_WINDOWS:
            print("  -> Jalankan: python -m ensurepip --upgrade")
            print("  -> Atau instal ulang Python dari https://www.python.org/downloads/windows/")
        elif IS_LINUX:
            print("  -> Jalankan: sudo apt update && sudo apt install -y python3-pip")
        sys.exit(1)


def _command_version(cmd_name: str) -> Optional[str]:
    resolved = shutil.which(cmd_name)
    if not resolved:
        return None
    try:
        res = run_cmd([resolved, "--version"], capture=True)
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return None


def check_and_install_node() -> None:
    node_ver = _command_version("node")
    npm_ver = _command_version("npm.cmd" if IS_WINDOWS else "npm")

    if node_ver and npm_ver:
        ok(f"Node.js ({node_ver}) dan npm ({npm_ver}) sudah terpasang.")
        return

    warn("Node.js dan/atau npm belum terpasang.")
    if IS_LINUX:
        if not shutil.which("apt-get") and not shutil.which("apt"):
            err("Manajer paket 'apt' tidak ditemukan. Instal Node.js & npm secara manual lalu jalankan ulang installer.")
            sys.exit(1)
        info("Mencoba instalasi otomatis Node.js dan npm via apt...")
        try:
            run_cmd(["sudo", "apt-get", "update"], check=True)
            run_cmd(["sudo", "apt-get", "install", "-y", "nodejs", "npm"], check=True)
        except subprocess.CalledProcessError as e:
            err(f"Gagal menginstal Node.js/npm secara otomatis (exit code {e.returncode}).")
            print("  -> Coba jalankan manual: sudo apt update && sudo apt install -y nodejs npm")
            sys.exit(1)
        except Exception as e:
            err(f"Error saat instalasi Node.js/npm: {e}")
            sys.exit(1)

        node_ver = _command_version("node")
        npm_ver = _command_version("npm")
        if not (node_ver and npm_ver):
            err("Node.js/npm masih belum terdeteksi setelah instalasi.")
            sys.exit(1)
        ok(f"Node.js ({node_ver}) dan npm ({npm_ver}) berhasil diinstal.")
    else:
        err("Node.js dan npm diperlukan untuk mem-build dashboard React frontend.")
        print("  -> Silakan unduh dan instal Node.js LTS dari: https://nodejs.org/en/download")
        print("  -> Setelah instalasi selesai, buka ulang terminal dan jalankan: python install.py")
        sys.exit(1)


def check_and_install_docker_linux() -> bool:
    """Linux-only: ensure Docker engine is installed, current user in docker group, and daemon running."""
    if not IS_LINUX:
        return False

    info("[Linux] Memeriksa instalasi Docker Engine (untuk dukungan Redroid)...")
    docker_bin = shutil.which("docker")
    group_added = False

    if not docker_bin:
        warn("Docker belum terdeteksi. Menjalankan instalasi otomatis dari https://get.docker.com ...")
        script_path = Path(tempfile.gettempdir()) / "get-docker.sh"
        try:
            if shutil.which("curl"):
                run_cmd(
                    ["curl", "-fsSL", "https://get.docker.com", "-o", str(script_path)],
                    check=True,
                )
            else:
                urllib.request.urlretrieve("https://get.docker.com", str(script_path))

            run_cmd(["sudo", "sh", str(script_path)], check=True)
            ok("Docker Engine berhasil diinstal.")
        except Exception as e:
            err(f"Gagal menginstal Docker secara otomatis: {e}")
            warn("Anda dapat menginstal Docker manual nanti dengan: curl -fsSL https://get.docker.com | sudo sh")
            return False
        finally:
            try:
                if script_path.exists():
                    script_path.unlink()
            except Exception:
                pass
    else:
        docker_ver = _command_version("docker") or "terpasang"
        ok(f"Docker sudah terpasang ({docker_ver}).")

    # Add current user to docker group
    current_user = os.environ.get("SUDO_USER") or getpass.getuser()
    if current_user and current_user != "root":
        try:
            groups_res = run_cmd(["id", "-nG", current_user], capture=True)
            user_groups = groups_res.stdout.split() if groups_res.returncode == 0 else []
            if "docker" not in user_groups:
                info(f"Menambahkan user '{current_user}' ke grup 'docker'...")
                run_cmd(["sudo", "usermod", "-aG", "docker", current_user], check=True)
                ok(f"User '{current_user}' berhasil ditambahkan ke grup 'docker'.")
                group_added = True
            else:
                ok(f"User '{current_user}' sudah tergabung dalam grup 'docker'.")
        except Exception as e:
            warn(f"Tidak dapat menambahkan user '{current_user}' ke grup docker: {e}")

    # Ensure Docker daemon is running (do NOT create any containers)
    try:
        if shutil.which("systemctl"):
            run_cmd(["sudo", "systemctl", "enable", "--now", "docker"], capture=True)
        elif shutil.which("service"):
            run_cmd(["sudo", "service", "docker", "start"], capture=True)
        ping = run_cmd(["sudo", "docker", "info"], capture=True)
        if ping.returncode == 0:
            ok("Docker daemon terverifikasi sedang berjalan (tanpa container baru dibuat).")
        else:
            warn("Docker terpasang namun daemon belum merespons. Cek dengan: sudo systemctl status docker")
    except Exception as e:
        warn(f"Gagal memverifikasi status Docker daemon: {e}")

    return group_added


def check_os_dependencies() -> bool:
    step_header(2, 6, "Pengecekan & Instalasi Dependensi OS")
    check_python_and_pip()
    check_and_install_node()
    return check_and_install_docker_linux()


# ---------------------------------------------------------------------------
# Step 3: Download & Setup Platform-Tools (ADB)
# ---------------------------------------------------------------------------

def _download_with_progress(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "NusukAutomationInstaller/2.0"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out_f:
        total = resp.headers.get("Content-Length")
        total_bytes = int(total) if total and total.isdigit() else 0
        downloaded = 0
        chunk_size = 1024 * 64
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            out_f.write(chunk)
            downloaded += len(chunk)
            if total_bytes > 0:
                pct = (downloaded / total_bytes) * 100
                mb_done = downloaded / (1024 * 1024)
                mb_total = total_bytes / (1024 * 1024)
                print(f"\r  [*] Mengunduh platform-tools: {mb_done:.1f}/{mb_total:.1f} MB ({pct:.1f}%)", end="", flush=True)
            else:
                mb_done = downloaded / (1024 * 1024)
                print(f"\r  [*] Mengunduh platform-tools: {mb_done:.1f} MB", end="", flush=True)
    print()


def _ensure_adb_executable_linux(adb_bin: Path) -> None:
    if IS_WINDOWS:
        return
    for candidate in (adb_bin, PLATFORM_TOOLS_DIR / "fastboot"):
        if candidate.exists():
            try:
                cur_mode = candidate.stat().st_mode
                candidate.chmod(cur_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except Exception as e:
                warn(f"Gagal mengatur permission chmod +x pada {candidate}: {e}")


def setup_platform_tools() -> Path:
    step_header(3, 6, "Download & Setup Android Platform-Tools (ADB)")
    adb_filename = "adb.exe" if IS_WINDOWS else "adb"
    adb_binary = PLATFORM_TOOLS_DIR / adb_filename

    if adb_binary.exists():
        ok(f"Binary ADB sudah ditemukan di: {adb_binary}")
        _ensure_adb_executable_linux(adb_binary)
        if not prompt_yes_no("Apakah Anda ingin mengunduh ulang & menimpa platform-tools?", default=False):
            return adb_binary

    sys_key = "Windows" if IS_WINDOWS else ("Linux" if IS_LINUX else platform.system())
    url = ADB_URLS.get(sys_key)
    if not url:
        err(f"OS '{sys_key}' tidak memiliki URL otomatis untuk Android platform-tools.")
        sys.exit(1)

    tmp_zip = Path(tempfile.gettempdir()) / f"platform-tools-{sys_key.lower()}.zip"
    try:
        info(f"Sumber unduhan: {url}")
        _download_with_progress(url, tmp_zip)

        if PLATFORM_TOOLS_DIR.exists():
            info("Membersihkan folder platform-tools lama...")
            shutil.rmtree(PLATFORM_TOOLS_DIR, ignore_errors=True)

        info(f"Mengekstrak ke {ROOT_DIR} ...")
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            names = zf.namelist()
            has_root_folder = any(n.startswith("platform-tools/") for n in names)
            if has_root_folder:
                zf.extractall(ROOT_DIR)
            else:
                PLATFORM_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
                zf.extractall(PLATFORM_TOOLS_DIR)

        if not adb_binary.exists():
            raise FileNotFoundError(f"Ekstraksi selesai namun {adb_binary} tidak ditemukan.")

        _ensure_adb_executable_linux(adb_binary)

        # Quick verification
        ver_res = run_cmd([str(adb_binary), "version"], capture=True)
        if ver_res.returncode == 0:
            first_line = ver_res.stdout.splitlines()[0] if ver_res.stdout else "OK"
            ok(f"ADB berhasil disiapkan: {adb_binary} ({first_line})")
        else:
            ok(f"ADB diekstrak ke: {adb_binary}")

        return adb_binary
    except urllib.error.URLError as e:
        err(f"Gagal mengunduh Android Platform-Tools (masalah jaringan): {e}")
        sys.exit(1)
    except Exception as e:
        err(f"Gagal menyiapkan Android Platform-Tools: {e}")
        sys.exit(1)
    finally:
        try:
            if tmp_zip.exists():
                tmp_zip.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Step 4: Setup Database & .env
# ---------------------------------------------------------------------------

def _parse_env_file(path: Path) -> Dict[str, str]:
    vals: Dict[str, str] = {}
    if not path.exists():
        return vals
    try:
        for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()
    except Exception:
        pass
    return vals


def setup_env_file() -> None:
    step_header(4, 6, "Konfigurasi Database MySQL & File .env")

    example_vals = _parse_env_file(ENV_EXAMPLE_FILE)
    existing_vals = _parse_env_file(ENV_FILE)

    def default_for(key: str, fallback: str) -> str:
        if key in existing_vals and existing_vals[key] != "":
            return existing_vals[key]
        return example_vals.get(key, fallback)

    info("Masukkan kredensial database MySQL Anda (tekan Enter untuk menggunakan nilai default):")
    db_host = prompt_input("DB_HOST", default_for("DB_HOST", "127.0.0.1"))
    db_port = prompt_input("DB_PORT", default_for("DB_PORT", "3306"))
    while not db_port.isdigit() or not (1 <= int(db_port) <= 65535):
        warn("DB_PORT harus berupa angka antara 1-65535.")
        db_port = prompt_input("DB_PORT", "3306")

    db_user = prompt_input("DB_USER", default_for("DB_USER", "root"))
    default_pw = existing_vals.get("DB_PASSWORD", example_vals.get("DB_PASSWORD", ""))
    pw_hint = " [Default: tersimpan]" if default_pw else " [Default: kosong]"
    entered_pw = input(f"  > DB_PASSWORD{pw_hint}: ")
    db_password = entered_pw if entered_pw != "" else default_pw

    db_name = prompt_input("DB_NAME", default_for("DB_NAME", "nusuk_key"))

    adb_rel_path = "platform-tools/adb.exe" if IS_WINDOWS else "platform-tools/adb"

    # Build .env preserving structure from .env.example + existing non-DB values
    overrides = {
        "DB_HOST": db_host,
        "DB_PORT": db_port,
        "DB_USER": db_user,
        "DB_PASSWORD": db_password,
        "DB_NAME": db_name,
        "ADB_PATH": adb_rel_path,
    }

    output_lines = []
    written_keys = set()

    if ENV_EXAMPLE_FILE.exists():
        template_lines = ENV_EXAMPLE_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
    else:
        template_lines = [
            "# Database Configuration (MySQL)",
            "DB_HOST=127.0.0.1",
            "DB_PORT=3306",
            "DB_USER=root",
            "DB_PASSWORD=",
            "DB_NAME=nusuk_key",
            "",
            "# ADB & Device Configuration",
            f"ADB_PATH={adb_rel_path}",
            "ADB_TARGET=127.0.0.1:5555",
            "NUSUK_PACKAGE=com.moh.nusukapp",
            "NUSUK_ACTIVITY=com.moh.nusukapp.activity.MainActivity",
            "",
            "# AI Provider Configuration",
            "AI_BASE_URL=https://api.openai.com/v1",
            "AI_API_KEY=your_ai_api_key_here",
            "AI_MODEL=gpt-4o-mini",
            "",
            "# Automation & Timing",
            "LOADING_TIMEOUT_SECONDS=25",
            "CIRCUIT_BREAKER_LIMIT=5",
            "ACTION_DELAY_SECONDS=0.0",
            "",
            "# Backend Server",
            "SERVER_HOST=0.0.0.0",
            "SERVER_PORT=8000",
        ]

    for line in template_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output_lines.append(line)
            continue
        k, v = stripped.split("=", 1)
        k = k.strip()
        if k in overrides:
            output_lines.append(f"{k}={overrides[k]}")
        elif k in existing_vals and existing_vals[k] != "":
            output_lines.append(f"{k}={existing_vals[k]}")
        else:
            output_lines.append(f"{k}={v.strip()}")
        written_keys.add(k)

    for k, v in overrides.items():
        if k not in written_keys:
            output_lines.append(f"{k}={v}")

    try:
        ENV_FILE.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
        ok(f"File .env berhasil dibuat/diperbarui di: {ENV_FILE}")
        info(f"ADB_PATH otomatis disetel ke: {adb_rel_path}")
    except Exception as e:
        err(f"Gagal menulis file .env: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Step 5: Install Project Dependencies (Python & React Frontend Build)
# ---------------------------------------------------------------------------

def ensure_root_requirements() -> Path:
    if REQUIREMENTS_FILE.exists():
        return REQUIREMENTS_FILE
    if BACKEND_REQUIREMENTS_FILE.exists():
        info("Menyalin backend/requirements.txt ke requirements.txt di root...")
        shutil.copy2(BACKEND_REQUIREMENTS_FILE, REQUIREMENTS_FILE)
        return REQUIREMENTS_FILE

    info("Membuat requirements.txt default di root proyek...")
    default_reqs = "\n".join([
        "fastapi>=0.115.0",
        "uvicorn>=0.30.0",
        "websockets>=12.0",
        "bcrypt>=4.0.0",
        "SQLAlchemy>=2.0.0",
        "pymysql>=1.1.0",
        "cryptography>=42.0.0",
        "pydantic>=2.0.0",
        "pydantic-settings>=2.0.0",
        "python-dotenv>=1.0.0",
        "uiautomator2>=3.0.0",
        "openai>=1.40.0",
        "requests>=2.31.0",
        "docker>=7.1.0",
        "",
    ])
    REQUIREMENTS_FILE.write_text(default_reqs, encoding="utf-8")
    return REQUIREMENTS_FILE


def _ensure_linux_venv() -> Optional[Path]:
    """Create/reuse a dedicated .venv on Linux to avoid Debian/Ubuntu apt-package conflicts (PEP 668 / RECORD errors)."""
    venv_dir = ROOT_DIR / ".venv"
    venv_python = venv_dir / "bin" / "python"
    if venv_python.exists():
        # Verify pip works inside existing venv
        chk = run_cmd([str(venv_python), "-m", "pip", "--version"], capture=True)
        if chk.returncode == 0:
            return venv_python
        shutil.rmtree(venv_dir, ignore_errors=True)

    info(f"Membuat virtual environment terisolasi di {venv_dir} (mencegah konflik paket Debian/Ubuntu)...")
    res = run_cmd([sys.executable, "-m", "venv", str(venv_dir)], capture=True)
    if res.returncode != 0:
        shutil.rmtree(venv_dir, ignore_errors=True)
        if shutil.which("apt-get"):
            py_minor = f"python{sys.version_info.major}.{sys.version_info.minor}-venv"
            warn(f"Modul venv belum terpasang. Menginstal {py_minor} / python3-venv via apt...")
            run_cmd(["sudo", "apt-get", "update"], capture=True)
            apt_res = run_cmd(["sudo", "apt-get", "install", "-y", py_minor, "python3-venv"])
            if apt_res.returncode == 0:
                res = run_cmd([sys.executable, "-m", "venv", str(venv_dir)], capture=True)

    if res.returncode == 0 and venv_python.exists():
        ok(f"Virtual environment siap: {venv_dir}")
        return venv_python
    return None


def install_python_dependencies() -> None:
    req_path = ensure_root_requirements()
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)

    target_python = sys.executable
    if IS_LINUX and not in_venv:
        venv_py = _ensure_linux_venv()
        if venv_py:
            target_python = str(venv_py)

    info(f"Menginstal dependensi Python dari {req_path.name} menggunakan {target_python} ...")
    cmd = [target_python, "-m", "pip", "install", "-r", str(req_path)]
    res = run_cmd(cmd, cwd=ROOT_DIR)

    if res.returncode != 0 and IS_LINUX and target_python == sys.executable:
        warn(
            "pip install global gagal (PEP 668 / konflik paket Debian seperti idna). "
            "Mencoba ulang dengan --break-system-packages --ignore-installed..."
        )
        res = run_cmd(
            [
                target_python,
                "-m",
                "pip",
                "install",
                "--break-system-packages",
                "--ignore-installed",
                "-r",
                str(req_path),
            ],
            cwd=ROOT_DIR,
        )

    if res.returncode != 0:
        err("Gagal menginstal dependensi Python. Periksa pesan error di atas.")
        sys.exit(1)
    ok("Seluruh dependensi Python backend berhasil diinstal.")


def install_and_build_frontend() -> None:
    if not FRONTEND_DIR.exists():
        err(f"Direktori frontend tidak ditemukan di: {FRONTEND_DIR}")
        sys.exit(1)

    info("Menginstal dependensi Node.js di frontend/ (npm install)...")
    try:
        if IS_WINDOWS:
            run_cmd("npm install", cwd=FRONTEND_DIR, shell=True, check=True)
        else:
            run_cmd(["npm", "install"], cwd=FRONTEND_DIR, check=True)
        ok("npm install selesai.")
    except subprocess.CalledProcessError as e:
        err(f"npm install gagal dengan kode keluar {e.returncode}.")
        sys.exit(1)

    info("Mem-build aplikasi React produksi di frontend/ (npm run build)...")
    try:
        if IS_WINDOWS:
            run_cmd("npm run build", cwd=FRONTEND_DIR, shell=True, check=True)
        else:
            run_cmd(["npm", "run", "build"], cwd=FRONTEND_DIR, check=True)
    except subprocess.CalledProcessError as e:
        err(f"npm run build gagal dengan kode keluar {e.returncode}.")
        sys.exit(1)

    dist_index = FRONTEND_DIR / "dist" / "index.html"
    if not dist_index.exists():
        err(f"Build selesai tetapi {dist_index} tidak ditemukan.")
        sys.exit(1)
    ok(f"Frontend berhasil di-build ke: {FRONTEND_DIR / 'dist'}")


def install_project_dependencies() -> None:
    step_header(5, 6, "Instalasi Dependensi Proyek (Python & Frontend Build)")
    install_python_dependencies()
    install_and_build_frontend()


# ---------------------------------------------------------------------------
# Step 6: Finalization
# ---------------------------------------------------------------------------

def finalize(docker_group_added: bool) -> None:
    step_header(6, 6, "Finalisasi Instalasi")
    py_cmd = "python" if IS_WINDOWS else "python3"
    banner("INSTALASI NUSUK AUTOMATION HUB v2.0 SELESAI!")
    ok("Seluruh komponen sistem, ADB platform-tools, konfigurasi .env, dan build frontend telah siap.")
    print(f"\n  Jalankan sistem sekarang dengan perintah:")
    print(f"    {py_cmd} run_system.py")
    print(f"\n  Mode akses cepat:")
    print(f"    - Local Only : {py_cmd} run_system.py --mode local")
    print(f"    - LAN / VPS  : {py_cmd} run_system.py --mode lan")
    print(f"    - Dev (Vite) : {py_cmd} run_system.py --dev")
    if IS_LINUX and docker_group_added:
        print("\n  [CATATAN LINUX DOCKER]")
        print("  User Anda baru saja ditambahkan ke grup 'docker'.")
        print("  Agar izin Docker aktif tanpa sudo, jalankan: newgrp docker")
        print("  atau logout lalu login kembali ke sesi SSH/terminal Anda.")
    print("=" * 70 + "\n")


def main() -> None:
    banner("NUSUK AUTOMATION HUB (v2.0) — UNIVERSAL INSTALLER")
    try:
        detect_host_specs()
        docker_group_added = check_os_dependencies()
        setup_platform_tools()
        setup_env_file()
        install_project_dependencies()
        finalize(docker_group_added)
    except KeyboardInterrupt:
        print("\n\n  [!] Instalasi dibatalkan oleh pengguna (Ctrl+C).")
        sys.exit(130)
    except Exception as exc:
        err(f"Terjadi kesalahan tidak terduga selama instalasi: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
