@echo off
title Locum ED HJ Auto-Booking Dashboard
cd /d "%~dp0"
echo Menjalankan Web Dashboard di http://127.0.0.1:8000 ...
echo Untuk akses dari HP (satu Wi-Fi), buka http://IP-PC-ANDA:8000
python web_app.py
pause

