#!/bin/bash

echo "=========================================="
echo "  Starting First-Time Installation..."
echo "  (Optimized for Raspberry Pi 5 / Debian)"
echo "=========================================="

echo "[1/5] Cleaning apt cache and updating repositories..."
sudo apt clean
sudo apt update

echo "[2/5] Enabling I2C Interface..."
sudo raspi-config nonint do_i2c 0

sudo apt-get install dnsmasq-base -y

echo "[3/5] Installing system dependencies via apt..."
sudo apt install -y python3-pip libgl1 libglib2.0-0 python3-lgpio swig python3-dev build-essential liblgpio-dev i2c-tools python3-libgpiod

echo "[4/5] Setting up Python Virtual Environment..."
python3 -m venv venv
source venv/bin/activate

echo "[5/5] Installing Python requirements via pip..."
pip install -r requirements.txt

echo "=========================================="
echo "  Installation Completed Successfully!"
echo "=========================================="
echo "To setup the auto-start service, run:"
./setup_service.sh
