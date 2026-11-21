# Nusuk Quiz & Key Automation Hub

Sistem otomasi interaksi aplikasi Android berbasis AI dan manajemen kunci (*key*) terdistribusi untuk fitur Challenge / Quiz pada aplikasi **Nusuk (نسك)** dengan Web Dashboard React modern.

---

## 🚀 Panduan Menjalankan Sistem (*Runbook*)

### 1. Prasyarat Lingkungan
* **Database MySQL XAMPP:** Pastikan MySQL aktif di port `3306` pada PC B. Database `nusuk_key` telah dibuat dan tabel-tabelnya terinisialisasi.
* **Emulator LDPlayer 9 (PC A):** Aktif pada alamat IP `192.168.100.8:5555` dan aplikasi Nusuk terpasang.
* **ADB Binary:** Menggunakan binary ADB bawaan di folder `platform-tools/adb.exe`.

### 2. Konfigurasi Lingkungan (`.env`)
Buka file `.env` di root proyek dan sesuaikan konfigurasi AI Provider dan database Anda:
```env
# Database Configuration (MySQL XAMPP)
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=
DB_NAME=nusuk_key

# ADB & Device Configuration
ADB_PATH=platform-tools/adb.exe
ADB_TARGET=192.168.100.8:5555
NUSUK_PACKAGE=com.moh.nusukapp
NUSUK_ACTIVITY=com.moh.nusukapp.activity.MainActivity

# AI Provider Configuration (OpenAI-Compatible: OpenAI, OpenRouter, Groq, DeepSeek, Together, Ollama, dll.)
AI_BASE_URL=https://api.openai.com/v1
AI_API_KEY=your_ai_api_key_here
AI_MODEL=gpt-4o-mini

# Automation & Timing
LOADING_TIMEOUT_SECONDS=25
CIRCUIT_BREAKER_LIMIT=5
ACTION_DELAY_SECONDS=1.0

# Backend Server
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
```
> **Catatan:** Anda dapat mengubah **AI Base URL**, **AI API Key**, dan **AI Model** secara langsung melalui antarmuka web di tombol **Settings (ikon gear)** pada Dashboard, serta melakukan **Test AI Connection** sebelum bot dijalankan.

---

### 3. Cara Menjalankan Sistem

#### Opsi A: Satu Perintah Sekaligus (*Interactive Runner*)
Jalankan script orkestrator di terminal root proyek:
```powershell
python run_system.py
```
Anda akan diberikan pilihan mode akses:
* **`[1] Local Only`:** Backend dan Frontend berjalan pada `127.0.0.1` (hanya dapat diakses di PC B ini).
* **`[2] LAN / Network`:** Backend dan Frontend berjalan pada `0.0.0.0` (dapat diakses dari PC atau HP lain di jaringan lokal yang sama). Alamat IP LAN PC B akan terdeteksi otomatis (misal: `http://192.168.100.42:5173`).

Anda juga dapat langsung menentukan mode via CLI:
```powershell
# Akses Lokal
python run_system.py --mode local

# Akses LAN (Jaringan)
python run_system.py --mode lan
```

#### Opsi B: Menjalankan Secara Terpisah
Jika ingin menjalankan backend dan frontend di dua jendela terminal berbeda:
* **Terminal 1 (Backend):**
  ```powershell
  python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
  ```
* **Terminal 2 (Frontend):**
  ```powershell
  cd frontend
  npm run dev -- --host 0.0.0.0 --port 5173
  ```

---

## 🎯 Fitur & Navigasi Web Dashboard

1. **Command Dashboard (`/`):**
   * **Header Global:** Status real-time MySQL, ADB `192.168.100.8:5555`, WebSocket telemetry, serta badge status bot (`IDLE`, `RUNNING`, `RECOVERING`, `STOPPING`) dengan timer uptime.
   * **Bot Command Center:**
     * Pemilih 3 Mode Operasi: `answer_only`, `redeem_only`, dan `auto`.
     * Tombol kendali: **START**, **STOP** (graceful stop aman), dan **Force Restart App** (membersihkan RAM dan membuka ulang aplikasi di PC A).
     * Live Activity Banner yang menampilkan nama layar terdeteksi dan deskripsi langkah aktif.
   * **Live Emulator Preview:** Pratinjau layar LDPlayer PC A secara live dengan saklar *Auto-Refresh (4s)* dan tombol tangkap layar manual.
   * **KPI Summary Cards:** Saldo kunci di akun Nusuk, stok key database, rasio sukses/invalid, akurasi AI kuis, dan jumlah self-healing.
   * **Visualisasi & Charts:** Tren soal & akurasi, laju redeem (used vs invalid), donat proporsi persediaan key, dan performa per kategori pintu.
2. **Key Vault:**
   * **Input Satuan (Manual):** Input 1 key dengan validasi duplikasi instan.
   * **Input Massal (Bulk Newline):** Textarea untuk paste banyak key dengan penghitung baris live dan laporan ringkasan impor.
   * **Tabel Inventaris Key:** Filter status, pencarian kode, tombol salin kode, reset status ke `unused`, dan ekspor daftar key `unused` ke `.txt`.
3. **Riwayat Kuis:**
   * Menampilkan audit log soal-soal yang dikerjakan oleh bot beserta jawaban pilihan AI dan status benar/salahnya.
   * Modal detail untuk melihat teks soal penuh dan keempat opsi pilihan ganda.
4. **Live System Logs:**
   * Konsol log bergaya terminal yang mengalir real-time via WebSocket.
   * Filter level log (`ALL`, `INFO`, `WARNING`, `ERROR / CRITICAL`) dan saklar *Auto-Scroll*.
5. **AI Provider & Settings Modal:**
   * Konfigurasi **AI Base URL**, **AI API Key**, dan **AI Model**.
   * Tombol **Test AI Connection** untuk memastikan provider AI merespons.
   * Pengaturan batas timeout, circuit breaker limit, delay aksi, dan uji koneksi ADB langsung.
