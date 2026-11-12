# Locum ED HJ — Auto Booking Bot (Web Dashboard + CLI + MySQL)

Sistem auto-booking untuk **Locum ED HJ Booking** (`https://kkah2026.github.io/locumedhj/`) dengan 2 cara penggunaan:
1. **Web Dashboard (PC & Mobile Responsive)** + database **MySQL / phpMyAdmin**
2. **CLI / CMD Interaktif** (`python auto_booking.py`) — tetap tersedia dan berfungsi penuh

## 1. Setup Database MySQL (phpMyAdmin)
- Secara default `.env` mengarah ke **XAMPP MariaDB (`127.0.0.1:3306`, user `root`, password kosong)** sesuai konfigurasi utama phpMyAdmin.
- Pastikan service **MySQL** di XAMPP Control Panel sudah dijalankan (atau ubah `MYSQL_PORT` / `MYSQL_PASSWORD` di `.env` jika memakai MySQL 8.0 port `3307`).
- Database `locum_booking` beserta tabel (`accounts`, `booking_jobs`, `booking_results`, `bot_logs`) otomatis dibuat saat dashboard dijalankan (atau bisa impor manual lewat `schema.sql`).

## 2. Menjalankan Web Dashboard (PC & Mobile)
Jalankan lewat CMD atau klik dua kali `start_dashboard.bat`:
```cmd
cd c:\Python\bot-auto-jadwal-kerja
python web_app.py
```
- **Akses di PC:** `http://127.0.0.1:8000`
- **Akses di HP (satu jaringan Wi-Fi):** `http://<IP-Lokal-PC>:8000`

### Fitur Dashboard
- **📅 Jadwal & Slot**: Lihat semua tanggal (`OPEN` / `FULL` / `BOOKED ✓`), booking langsung slot `OPEN`, atau klik **Atur Bot Tanggal Ini**.
- **🤖 Atur Auto-Book**: Pilih target tanggal (termasuk **Tanggal Baru Tengah Malam +30 hari**), pilih jam shift sesuai urutan prioritas (`#1`, `#2`, dst.), lalu atur:
  - **Set Jam & Menit Spesifik** (`MYT UTC+8` atau `WIB UTC+7`), mis. `00:00 MYT` (`23:00 WIB`) tepat saat slot baru dibuka.
  - **Pantau Sekarang**: cek berkala untuk menangkap slot batalan.
  - Toggle **Mode Simulasi (Dry-Run)** & **Maksimal 1 shift/hari**.
- **📋 Booking Saya**: Lihat booking aktif (dengan tombol Cancel) & riwayat hasil booking dari MySQL.
- **📜 Log Bot**: Pantau log aktivitas real-time dari tabel `bot_logs` di MySQL.

## 3. Menjalankan Mode CMD Interaktif
```cmd
python auto_booking.py
```
