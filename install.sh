#!/bin/bash

echo "=========================================="
echo "  Starting First-Time Installation..."
echo "=========================================="

# 1. อัปเดตรายการแพ็กเกจของระบบ
echo "[1/4] Updating apt repositories..."
sudo apt update

# 2. ติดตั้ง System Dependencies (apt)
echo "[2/4] Installing system dependencies via apt..."
# libgl1-mesa-glx และ libglib2.0-0 จำเป็นสำหรับ OpenCV
# python3-lgpio จำเป็นสำหรับ ควบคุม GPIO ผ่าน lgpio factory บน Raspberry Pi
sudo apt install -y python3-pip libgl1-mesa-glx libglib2.0-0 python3-lgpio

# 3. สร้าง Virtual Environment (แนะนำสำหรับ Python รุ่นใหม่ เพื่อป้องกันปัญหากับ System Packages)
echo "[3/4] Setting up Python Virtual Environment..."
python3 -m venv venv
source venv/bin/activate

# 4. ติดตั้ง Python Libraries จาก requirements.txt
echo "[4/4] Installing Python requirements via pip..."
pip install -r requirements.txt

echo "=========================================="
echo "  Installation Completed Successfully!"
echo "=========================================="
echo "To run the project, use the following commands:"
echo "  source venv/bin/activate"
echo "  python app.py"
