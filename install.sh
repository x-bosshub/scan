#!/bin/bash

echo "=========================================="
echo "  Starting First-Time Installation..."
echo "  (Optimized for Raspberry Pi 5 / Debian)"
echo "=========================================="

# 1. ล้างแคชของ apt เผื่อกรณีไฟล์พังค้าง และอัปเดตรายการแพ็กเกจ
echo "[1/4] Cleaning apt cache and updating repositories..."
sudo apt clean
sudo apt update

# 2. ติดตั้ง System Dependencies (apt) แบบครบถ้วน
# - libgl1 และ libglib2.0-0 : สำหรับระบบจัดการภาพของ OpenCV
# - python3-lgpio, swig, python3-dev, build-essential, liblgpio-dev : สำหรับบิลด์ระบบ GPIO
echo "[2/4] Installing system dependencies via apt..."
sudo apt install -y python3-pip libgl1 libglib2.0-0 python3-lgpio swig python3-dev build-essential liblgpio-dev

# 3. สร้างและเปิดใช้งาน Virtual Environment
echo "[3/4] Setting up Python Virtual Environment..."
python3 -m venv venv
source venv/bin/activate

# 4. ติดตั้ง Python Libraries จาก requirements.txt
echo "[4/4] Installing Python requirements via pip..."
pip install -r requirements.txt

echo "=========================================="
echo "  Installation Completed Successfully!"
echo "=========================================="
echo "To setup the auto-start service, run:"
echo "  ./setup_service.sh"
