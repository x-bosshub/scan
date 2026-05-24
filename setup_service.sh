#!/bin/bash

# กำหนดชื่อ Service
SERVICE_NAME="iot-scanner.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"

# ดึงตำแหน่ง Path ของโฟลเดอร์ที่สคริปต์นี้ตั้งอยู่โดยอัตโนมัติแบบ 100% (แม่นยำสูงสุด)
PROJECT_DIR=$(cd "$(dirname "$0")" && pwd)

echo "=========================================="
echo "  Creating systemd service: $SERVICE_NAME"
echo "  Detected Project Directory: $PROJECT_DIR"
echo "=========================================="

echo "[1/3] Generating service file at $SERVICE_PATH..."

# สร้างไฟล์ .service โดยใช้ Path และ VENV ปัจจุบันที่คำนวณได้อัตโนมัติ
# กำหนด User=root เพื่อป้องกันปัญหาการเข้าถึง GPIO และ Network Ports
sudo bash -c "cat > $SERVICE_PATH" <<EOF
[Unit]
Description=IoT Network Scanner and RTSP Stream Service
After=network.target

[Service]
User=root
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/venv/bin/python $PROJECT_DIR/app.py
StandardOutput=inherit
StandardError=inherit
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "[2/3] Reloading systemd daemon..."
sudo systemctl daemon-reload

echo "[3/3] Enabling and starting the service..."
sudo systemctl enable $SERVICE_NAME
sudo systemctl start $SERVICE_NAME

echo "=========================================="
echo "  Service setup completed successfully!"
echo "=========================================="
echo "Commands for managing the service:"
echo "  Check status : sudo systemctl status $SERVICE_NAME"
echo "  Stop service : sudo systemctl stop $SERVICE_NAME"
echo "  Start service: sudo systemctl start $SERVICE_NAME"
echo "  View logs    : sudo journalctl -u $SERVICE_NAME -f"
