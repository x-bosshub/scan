#!/bin/bash

echo "=========================================="
echo "  Starting First-Time Installation..."
echo "=========================================="

# 1. อัปเดตรายการแพ็กเกจของระบบ
echo "[1/4] Updating apt repositories..."
sudo apt update

# 2. ติดตั้ง System Dependencies (apt)
echo "[2/4] Installing system dependencies via apt..."
sudo apt install -y python3-pip libgl1 libglib2.0-0 python3-lgpio

# 3. สร้าง Virtual Environment
echo "[3/4] Setting up Python Virtual Environment..."
python3 -m venv venv
source venv/bin/activate

# 4. ติดตั้ง Python Libraries จาก requirements.txt
echo "[4/4] Installing Python requirements via pip..."
pip install -r requirements.txt

echo "=========================================="
echo "  Installation Completed Successfully!"
echo "=========================================="
