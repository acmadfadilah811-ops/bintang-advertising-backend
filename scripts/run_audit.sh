#!/bin/bash

# Pindah ke root directory proyek (satu tingkat di atas folder script)
cd "$(dirname "$0")/.."

# Script ini otomatis mendeteksi virtualenv, menjalankan Django check --deploy, 
# serta memasang dan menjalankan Ruff (linter) & Bandit (security scanner).


echo "=========================================================="
echo "      Memulai Audit Keamanan & Kualitas Backend Django    "
echo "=========================================================="
echo ""

# 1. Deteksi & Aktivasi Environment (Mendukung uv dan virtualenv biasa)
USE_UV=false

if command -v uv &> /dev/null; then
    echo "[+] Menemukan alat 'uv' terinstal di sistem."
    USE_UV=true
fi

if [ "$USE_UV" = true ]; then
    echo "[+] Menggunakan 'uv' untuk eksekusi audit (lebih cepat & bersih)."
else
    if [ -d ".venv" ]; then
        echo "[+] Menemukan virtual environment (.venv). Mengaktifkan..."
        source .venv/bin/activate
    elif [ -d "venv" ]; then
        echo "[+] Menemukan virtual environment (venv). Mengaktifkan..."
        source venv/bin/activate
    else
        echo "[!] Virtual environment tidak ditemukan secara otomatis."
    fi
fi

echo ""
# 2. Jalankan Django Deployment Check
echo "----------------------------------------------------------"
echo "[1/3] Menjalankan Django Deployment Check..."
echo "----------------------------------------------------------"
if [ "$USE_UV" = true ]; then
    uv run python manage.py check --deploy
else
    python manage.py check --deploy
fi
echo ""

# 3. Jalankan Ruff (Linter)
echo "----------------------------------------------------------"
echo "[2/3] Menjalankan Linter (Ruff)..."
echo "----------------------------------------------------------"
if [ "$USE_UV" = true ]; then
    # uvx (uv tool run) menjalankan ruff secara terisolasi tanpa mengotori venv proyek
    uvx ruff check . --exclude .venv,venv,migrations
else
    if ! command -v ruff &> /dev/null; then
        echo "[+] Ruff belum terinstal. Menginstal ruff..."
        pip install ruff --quiet
    fi
    ruff check . --exclude .venv,venv,migrations
fi
echo "[+] Scan Ruff selesai."
echo ""

# 4. Jalankan Bandit (Security Scanner)
echo "----------------------------------------------------------"
echo "[3/3] Menjalankan Security Scanner (Bandit)..."
echo "----------------------------------------------------------"
if [ "$USE_UV" = true ]; then
    # uvx menjalankan bandit secara terisolasi
    uvx bandit -r . -x ./.venv,./venv,./tests.py,./api/tests.py -ll
else
    if ! command -v bandit &> /dev/null; then
        echo "[+] Bandit belum terinstal. Menginstal bandit..."
        pip install bandit --quiet
    fi
    bandit -r . -x ./.venv,./venv,./tests.py,./api/tests.py -ll
fi
echo ""
echo "=========================================================="
echo "               Proses Audit Selesai                       "
echo "=========================================================="
