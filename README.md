# Install & Setup
```
git clone https://github.com/x-bosshub/scan.git
cd scan
chmod +x install.sh setup_service.sh
./install.sh
./setup_service.sh



# 1. เข้าสู่ Virtual Environment
source venv/bin/activate

# 2. ติดตั้งเครื่องมือพื้นฐานและไลบรารีจัดการระบบ
sudo apt-get update
sudo apt-get install -y i2c-tools cmake python3-opencv

# 3. ติดตั้งไลบรารี Python ทั้งหมดที่ระบบ Kiosk นี้ต้องใช้
pip install Flask psutil requests smbus2 gpiozero adafruit-circuitpython-servokit

# 4. ติดตั้งไลบรารี AI (การติดตั้ง dlib และ face_recognition อาจใช้เวลาสักพัก)
pip install numpy face_recognition ultralytics

# 5. ออกจาก venv และรีสตาร์ท Service
deactivate
sudo systemctl restart iot-scanner.service

```
# สามารถเข้าใช้งาน
```
http://localhost:8000
```

# 1. ดาวน์โหลดโค้ดจาก GitHub ของคุณ
```
git clone https://github.com/x-bosshub/scan.git
```

# 2. เข้าไปในโฟลเดอร์ที่เพิ่งโหลดมา
```
cd scan
```
# 3. เปิดสิทธิ์ให้ไฟล์สคริปต์สามารถรันได้
```
chmod +x install.sh setup_service.sh
```
# 4. สั่งติดตั้งระบบและไลบรารี (อาจมีถามรหัสผ่านของเครื่อง Pi ให้พิมพ์แล้วกด Enter)
```
./install.sh
```
# 5. ติดตั้ง Service ให้โปรแกรมรันเองทุกครั้งที่เปิดเครื่อง Pi 5
```
./setup_service.sh
```



# ​ซ่อมแซมระบบ APT ที่พัง (Clear Cache)
​รันคำสั่ง 3 บรรทัดนี้ใน Terminal ทีละบรรทัด เพื่อล้างไฟล์ที่พังและดาวน์โหลดรายชื่อใหม่:
```
sudo rm -rf /var/lib/apt/lists/*
sudo apt clean
sudo apt update
```
