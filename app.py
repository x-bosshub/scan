import socket
import re
import cv2
from flask import Flask, render_template, jsonify, request, Response
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import sys
import psutil
import os
import json
import subprocess
from gpiozero import AngularServo
from gpiozero.pins.lgpio import LGPIOFactory
from time import sleep

# ==========================================
# โหลดไลบรารี Face Recognition
# ==========================================
try:
    import face_recognition
    import numpy as np
    face_rec_available = True
    print("[SYSTEM] โหลดไลบรารี Face Recognition สำเร็จ")
except ImportError:
    face_rec_available = False
    print("[WARNING] ไม่พบไลบรารี face_recognition หรือ numpy กรุณาติดตั้งเพื่อเปิดใช้งานระบบจดจำใบหน้า")

# ==========================================
# โหลดไลบรารี Telegram (Requests)
# ==========================================
try:
    import requests
    requests_available = True
except ImportError:
    requests_available = False
    print("[WARNING] ไม่พบไลบรารี requests กรุณาติดตั้งเพื่อใช้งาน Telegram (pip install requests)")

# ==========================================
# โหลดไลบรารี Raspbot (Custom Board)
# ==========================================
try:
    from Raspbot_Lib import Raspbot
    raspbot_car = Raspbot()
    raspbot_available = True
    print("[SYSTEM] โหลดไลบรารี Raspbot_Lib สำเร็จ (พร้อมสั่งงานผ่าน I2C)")
except Exception as e:
    raspbot_car = None
    raspbot_available = False
    print(f"[WARNING] ไม่พบไลบรารี Raspbot_Lib หรือบอร์ด Raspbot ไม่พร้อมทำงาน: {e}")

factory = LGPIOFactory()

# ระบบตรวจจับบอร์ด Servo 16 ช่อง (PCA9685)
try:
    from adafruit_servokit import ServoKit
    pca_kit = ServoKit(channels=16)
    pca_available = True
    print("[SYSTEM] ตรวจพบบอร์ด PCA9685 16-Channel Servo")
except Exception as e:
    pca_kit = None
    pca_available = False
    print(f"[WARNING] ไม่พบบอร์ด PCA9685 หรือยังไม่ได้เปิด I2C: {e}")

app = Flask(__name__) 

# ตัวแปร Global จัดการ Servo
servos_config = {
    "1": {"connected": False, "type": "gpio", "obj": None, "pin": None, "angle": 90, "name": "ซ้าย-ขวา"},
    "2": {"connected": False, "type": "gpio", "obj": None, "pin": None, "angle": 90, "name": "ขึ้น-ลง"}
}

CONFIG_FILE = 'servo_config.json'

# ==========================================
# TELEGRAM NOTIFICATION SYSTEM
# ==========================================
TELEGRAM_CONFIG = 'telegram_config.json'
telegram_settings = {
    "bot_token": "",
    "chat_id": "",
    "enabled": False,
    "cooldown": 30
}
last_alert_time = 0

def load_telegram_config():
    global telegram_settings
    if os.path.exists(TELEGRAM_CONFIG):
        try:
            with open(TELEGRAM_CONFIG, 'r', encoding='utf-8') as f:
                saved_data = json.load(f)
                telegram_settings.update(saved_data)
            print("[SYSTEM] โหลดการตั้งค่า Telegram สำเร็จ")
        except Exception as e:
            print(f"[ERROR] ไม่สามารถโหลดไฟล์ Telegram Config ได้: {e}")
    else:
        save_telegram_config()

def save_telegram_config():
    try:
        with open(TELEGRAM_CONFIG, 'w', encoding='utf-8') as f:
            json.dump(telegram_settings, f, indent=4)
    except Exception as e:
        print(f"[ERROR] ไม่สามารถบันทึกไฟล์ Telegram Config ได้: {e}")

def send_telegram_alert(frame_img, message):
    global telegram_settings
    if not requests_available or not telegram_settings["enabled"]:
        return
    if not telegram_settings["bot_token"] or not telegram_settings["chat_id"]:
        return
        
    try:
        ret, buffer = cv2.imencode('.jpg', frame_img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ret: return
        
        url = f"https://api.telegram.org/bot{telegram_settings['bot_token']}/sendPhoto"
        files = {'photo': ('alert.jpg', buffer.tobytes(), 'image/jpeg')}
        data = {'chat_id': telegram_settings['chat_id'], 'caption': message}
        
        requests.post(url, files=files, data=data, timeout=10)
        print(f"[TELEGRAM] ส่งการแจ้งเตือนสำเร็จ: {message}")
    except Exception as e:
        print(f"[ERROR] ไม่สามารถส่ง Telegram ได้: {e}")

# ==========================================
# FACE RECOGNITION DATABASE & INIT
# ==========================================
FACE_DIR = 'dataset/faces'
FACE_CONFIG = 'known_faces.json'
known_face_encodings = []
known_face_names = []

if not os.path.exists(FACE_DIR):
    os.makedirs(FACE_DIR)

def load_known_faces():
    global known_face_encodings, known_face_names
    if not face_rec_available:
        return
        
    known_face_encodings = []
    known_face_names = []
    
    if os.path.exists(FACE_CONFIG):
        try:
            with open(FACE_CONFIG, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            for item in data:
                name = item['name']
                img_path = item['image_path']
                if os.path.exists(img_path):
                    image = face_recognition.load_image_file(img_path)
                    encodings = face_recognition.face_encodings(image)
                    if len(encodings) > 0:
                        known_face_encodings.append(encodings[0])
                        known_face_names.append(name)
            print(f"[SYSTEM] โหลดข้อมูลใบหน้าสำเร็จ: จำนวน {len(known_face_names)} ใบหน้า")
        except Exception as e:
            print(f"[ERROR] ไม่สามารถโหลดไฟล์ข้อมูลใบหน้าได้: {e}")

# ==========================================
# AUTO SAVE/LOAD SYSTEM สำหรับ Servo
# ==========================================
def save_servo_config():
    try:
        data_to_save = {
            "1": {"type": servos_config["1"]["type"], "pin": servos_config["1"]["pin"]},
            "2": {"type": servos_config["2"]["type"], "pin": servos_config["2"]["pin"]}
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(data_to_save, f)
        print("[SYSTEM] บันทึกการตั้งค่า Servo สำเร็จ")
    except Exception as e:
        print(f"[ERROR] ไม่สามารถบันทึกไฟล์ตั้งค่าได้: {e}")

def load_servo_config():
    global servos_config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                saved_data = json.load(f)
            
            for sid in ["1", "2"]:
                if sid in saved_data and saved_data[sid]["pin"] is not None:
                    conn_type = saved_data[sid]["type"]
                    pin = int(saved_data[sid]["pin"])
                    
                    servos_config[sid]["type"] = conn_type
                    servos_config[sid]["pin"] = pin
                    servos_config[sid]["angle"] = 90
                    
                    if conn_type == "pca9685" and pca_available:
                        pca_kit.servo[pin].angle = 90
                        servos_config[sid]["connected"] = True
                    elif conn_type == "raspbot" and raspbot_available:
                        raspbot_car.Ctrl_Servo(pin, 90)
                        servos_config[sid]["connected"] = True
                    elif conn_type == "gpio":
                        servos_config[sid]["obj"] = AngularServo(pin, min_angle=0, max_angle=180, pin_factory=factory)
                        servos_config[sid]["obj"].angle = 90
                        servos_config[sid]["connected"] = True
                        
            print("[SYSTEM] โหลดการตั้งค่า Servo ล่าสุดสำเร็จ")
        except Exception as e:
            print(f"[ERROR] ไฟล์ตั้งค่าเสียหาย หรือโหลดไม่สำเร็จ: {e}")

# ==========================================
# WIRELESS MANAGEMENT (WI-FI, AP FALLBACK & BLUETOOTH)
# ==========================================

def enable_ap_mode():
    print("[SYSTEM] เครือข่ายไม่มีการเชื่อมต่อ กำลังเปิดโหมด AP (Hotspot)...")
    try:
        subprocess.run(['sudo', 'nmcli', 'device', 'disconnect', 'wlan0'], capture_output=True)
        subprocess.run(['sudo', 'nmcli', 'connection', 'delete', 'Pi5_Hotspot'], capture_output=True)
        subprocess.run(['sudo', 'nmcli', 'connection', 'delete', 'Hotspot'], capture_output=True)
        time.sleep(2)
        subprocess.run(['sudo', 'nmcli', 'connection', 'add', 'type', 'wifi', 'ifname', 'wlan0', 'con-name', 'Pi5_Hotspot', 'autoconnect', 'yes', 'ssid', 'Pi5_Setup'], capture_output=True)
        subprocess.run(['sudo', 'nmcli', 'connection', 'modify', 'Pi5_Hotspot', '802-11-wireless.mode', 'ap', '802-11-wireless.band', 'bg', 'ipv4.method', 'shared'], capture_output=True)
        subprocess.run(['sudo', 'nmcli', 'connection', 'modify', 'Pi5_Hotspot', 'wifi-sec.key-mgmt', 'wpa-psk', 'wifi-sec.psk', '12345678'], capture_output=True)
        res = subprocess.run(['sudo', 'nmcli', 'connection', 'up', 'Pi5_Hotspot'], capture_output=True, text=True)
        
        if res.returncode == 0:
            print("[SYSTEM] เปิด AP Mode สำเร็จ: SSID=Pi5_Setup, Password=12345678")
        else:
            print(f"[ERROR] ไม่สามารถเปิด AP Mode ได้: {res.stderr}")
    except Exception as e:
        print(f"[ERROR] เกิดข้อผิดพลาดขณะเปิด AP Mode: {e}")

def check_network_and_fallback():
    print("[SYSTEM] กำลังตรวจสอบสถานะเครือข่าย...")
    ip = get_local_ip()
    if ip != '127.0.0.1':
        print(f"[SYSTEM] พบการเชื่อมต่อเครือข่ายสำเร็จ IP ปัจจุบัน: {ip}")
        return

    print("[SYSTEM] ไม่พบ IP, กำลังพยายามเปิดการค้นหา Wi-Fi และเชื่อมต่ออัตโนมัติ...")
    try:
        subprocess.run(['sudo', 'nmcli', 'radio', 'wifi', 'on'], capture_output=True)
        time.sleep(1)
        subprocess.run(['sudo', 'nmcli', 'dev', 'wifi', 'rescan'], capture_output=True)
        time.sleep(4) 
    except Exception as e:
        print(f"[WARNING] ไม่สามารถสั่ง rescan wifi ได้: {e}")

    ip = get_local_ip()
    if ip == '127.0.0.1':
        print("[SYSTEM] ไม่สามารถเชื่อมต่อ Wi-Fi ที่บันทึกไว้ได้")
        enable_ap_mode()
    else:
        print(f"[SYSTEM] เชื่อมต่อ Wi-Fi เดิมสำเร็จ IP ปัจจุบัน: {ip}")

@app.route('/api/wifi/scan')
def wifi_scan():
    try:
        try:
            subprocess.run(['sudo', 'nmcli', 'dev', 'wifi', 'rescan'], timeout=5, capture_output=True)
            time.sleep(2) 
        except subprocess.TimeoutExpired:
            pass 
            
        res = subprocess.check_output(['sudo', 'nmcli', '-t', '-f', 'SSID,SIGNAL,SECURITY', 'dev', 'wifi'], timeout=20).decode('utf-8')
        networks = []
        for line in res.split('\n'):
            if line.strip():
                parts = line.split(':')
                if len(parts) >= 3 and parts[0]: 
                    networks.append({'ssid': parts[0], 'signal': int(parts[1]), 'security': parts[2]})
        
        unique_nets = {}
        for n in networks:
            if n['ssid'] not in unique_nets or n['signal'] > unique_nets[n['ssid']]['signal']:
                unique_nets[n['ssid']] = n
                
        return jsonify({"status": "ok", "networks": list(unique_nets.values())})
        
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "message": "หมดเวลาในการสแกน (Timeout) โมดูล Wi-Fi อาจกำลังยุ่ง กรุณากด Rescan อีกครั้ง", "networks": []}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "networks": []}), 500

@app.route('/api/wifi/connect', methods=['POST'])
def wifi_connect():
    data = request.get_json()
    ssid = data.get('ssid')
    password = data.get('password')
    try:
        subprocess.run(['sudo', 'nmcli', 'connection', 'down', 'Pi5_Hotspot'], capture_output=True)
        subprocess.run(['sudo', 'nmcli', 'connection', 'delete', 'Pi5_Hotspot'], capture_output=True)
        subprocess.run(['sudo', 'nmcli', 'connection', 'down', 'Hotspot'], capture_output=True)
        subprocess.run(['sudo', 'nmcli', 'connection', 'delete', 'Hotspot'], capture_output=True)
        time.sleep(2)
        
        if password:
            result = subprocess.run(['sudo', 'nmcli', 'dev', 'wifi', 'connect', ssid, 'password', password], capture_output=True, text=True, timeout=20)
        else:
            result = subprocess.run(['sudo', 'nmcli', 'dev', 'wifi', 'connect', ssid], capture_output=True, text=True, timeout=20)
            
        if result.returncode == 0:
            return jsonify({"status": "ok", "message": f"เชื่อมต่อ {ssid} สำเร็จ"})
        else:
            check_network_and_fallback()
            return jsonify({"status": "error", "message": result.stderr})
    except Exception as e:
        check_network_and_fallback()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/bt/scan')
def bt_scan():
    try:
        subprocess.run(['bluetoothctl', '--timeout', '5', 'scan', 'on'], capture_output=True)
        res = subprocess.check_output(['bluetoothctl', 'devices']).decode('utf-8')
        devices = []
        for line in res.split('\n'):
            if line.startswith('Device '):
                parts = line.split(' ', 2)
                if len(parts) >= 3:
                    mac = parts[1]
                    name = parts[2]
                    if '-' not in name: 
                        devices.append({"mac": mac, "name": name})
        return jsonify({"status": "ok", "devices": devices})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "devices": []}), 500

@app.route('/api/bt/connect', methods=['POST'])
def bt_connect():
    data = request.get_json()
    mac = data.get('mac')
    try:
        subprocess.run(['bluetoothctl', 'pair', mac], timeout=10)
        result = subprocess.run(['bluetoothctl', 'connect', mac], capture_output=True, text=True, timeout=10)
        if "Successful" in result.stdout or result.returncode == 0:
            return jsonify({"status": "ok", "message": "จับคู่สำเร็จ"})
        else:
            return jsonify({"status": "error", "message": "เชื่อมต่อไม่สำเร็จ ดูที่อุปกรณ์ปลายทาง"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ==========================================
# NETWORK CORE FUNCTIONS
# ==========================================
COMMON_PORTS = [
    21, 22, 23, 43, 53, 80, 81, 111, 443,
    554, 1935,
    1883, 8883,
    3306, 5432, 27017,
    3000, 4000, 5000, 6080, 7681, 7000,
    8000, 8008, 8009, 8080, 8081, 8090, 8092,
    8200, 8443, 8899, 9000, 9080,
    34567, 37777, 37778, 37779
]

MAC_VENDORS = {
    "DC:A6:32": "Raspberry Pi", "B8:27:EB": "Raspberry Pi", "E4:5F:01": "Raspberry Pi", "28:CD:C1": "Raspberry Pi",
    "D8:3A:DD": "Espressif", "24:0A:C4": "Espressif", "30:AE:A4": "Espressif", "AC:67:B2": "Espressif", "60:01:94": "Espressif",
    "F4:F5:DB": "Apple", "AE:60:5C": "Apple",
    "00:11:32": "Synology", "00:E0:4C": "Realtek",
    "48:EA:63": "Dahua", "E0:50:8B": "Hikvision", "10:12:48": "Hikvision",
    "00:0C:29": "VMware", "00:50:56": "VMware"
}

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        IP = s.getsockname()[0]
    except:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

def get_mac_address(ip):
    try:
        with open('/proc/net/arp', 'r') as f:
            data = f.read()
        pattern = re.compile(re.escape(ip) + r'\s+\w+\s+\w+\s+([0-9a-fA-F:]+)\s+')
        match = pattern.search(data)
        if match: return match.group(1).upper()
    except:
        pass
    return None

def resolve_vendor(mac):
    if not mac: return ""
    for oui, vendor in MAC_VENDORS.items():
        if mac.startswith(oui): return vendor
    return ""

def identify_device_type(ports, vendor):
    if 37777 in ports: return "Dahua Device", "fa-video", "bg-camera"
    if 34567 in ports: return "XMeye/China DVR", "fa-video", "bg-camera"
    if 8200 in ports: return "Hikvision Device", "fa-video", "bg-camera"
    if 554 in ports or 1935 in ports: return "IP Camera/NVR", "fa-video", "bg-camera"
    
    if 1883 in ports: return "MQTT Broker/IoT", "fa-microchip", "bg-iot"
    if 3306 in ports or 5432 in ports: return "Database Server", "fa-database", "bg-warning text-dark"
    if 9000 in ports: return "Portainer/Docker", "fa-docker", "bg-info text-dark"

    if 22 in ports and ("Raspberry" in vendor or "Linux" in vendor): return "Linux Server", "fa-server", "bg-server"
    if 111 in ports: return "Unix/Linux Device", "fa-linux", "bg-server"

    if any(p in ports for p in [80, 443, 8000, 4000, 8080, 3000, 5000, 6080, 7681]): return "Web Server/App", "fa-globe", "bg-light text-dark"
    if "Espressif" in vendor: return "ESP32 Device", "fa-microchip", "bg-iot"
    if "Apple" in vendor: return "Apple Device", "fa-apple", "bg-light text-dark"
    if "Synology" in vendor: return "NAS Storage", "fa-hdd", "bg-secondary text-white"

    return "Network Device", "fa-network-wired", "bg-light text-dark"

def check_port(ip, port, timeout=0.0):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        res = sock.connect_ex((ip, port))
        sock.close()
        return port if res == 0 else None
    except:
        sock.close() 
        return None

def quick_scan_host(ip):
    active_ports = []
    is_up = False
    scan_timeout = 0.05
    
    for port in COMMON_PORTS:
        if check_port(ip, port, timeout=scan_timeout):
            active_ports.append(port)
            is_up = True
    
    if is_up:
        try: 
            hostname = socket.gethostbyaddr(ip)[0]
        except: 
            hostname = ""
        mac = get_mac_address(ip)
        vendor = resolve_vendor(mac)
        type_name, icon, badge_class = identify_device_type(active_ports, vendor)
        return {
            'ip': ip, 'hostname': hostname, 'mac': mac, 
            'vendor': vendor, 'ports': active_ports,
            'type': type_name, 'icon': icon, 'badge_class': badge_class
        }
    return None

# ==========================================
# PROCESS MOTION DETECTION (อัปเดตลดความเซนซิทีฟสำหรับ Outdoor)
# ==========================================
def process_motion_detection(frame, previous_frame):
    global last_alert_time, telegram_settings
    
    # ย่อสัดส่วนชั่วคราวเพื่อหาการเคลื่อนไหว
    small_frame = cv2.resize(frame, (500, int(500 * frame.shape[0] / frame.shape[1])))
    gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)
    
    # [จุดที่แก้ 1] เพิ่มการเบลอ (Gaussian Blur) จาก 21 เป็น 31 เพื่อลบรายละเอียดเล็กๆ เช่น ใบไม้ขยับ หรือสัญญาณภาพซ่า
    gray = cv2.GaussianBlur(gray, (31, 31), 0)

    if previous_frame is None:
        return frame, gray

    # หาความแตกต่างของเฟรม
    frame_delta = cv2.absdiff(previous_frame, gray)
    
    # [จุดที่แก้ 2] เพิ่ม Threshold จาก 25 เป็น 40 (พิกเซลต้องมีสี/แสงต่างจากเดิมค่อนข้างชัดเจน ถึงจะนับว่าขยับ)
    thresh = cv2.threshold(frame_delta, 40, 255, cv2.THRESH_BINARY)[1]
    thresh = cv2.dilate(thresh, None, iterations=2)
    contours, _ = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    motion_detected = False
    for contour in contours:
        # [จุดที่แก้ 3] เพิ่มขนาดวัตถุขั้นต่ำ จาก 1500 เป็น 5000 
        # (ถ้าวัตถุเล็กกว่านี้ เช่น นกบินผ่านไกลๆ หรือกิ่งไม้ไหว จะถูกมองข้าม)
        if cv2.contourArea(contour) < 5000:
            continue
            
        motion_detected = True
        (x, y, w, h) = cv2.boundingRect(contour)
        
        # ขยายสเกลกลับไปวาดกรอบบนเฟรมจริง
        scale_x = frame.shape[1] / 500
        scale_y = frame.shape[0] / small_frame.shape[0]
        rx, ry, rw, rh = int(x * scale_x), int(y * scale_y), int(w * scale_x), int(h * scale_y)
        
        # วาดกรอบสีเหลืองรอบวัตถุที่ขยับ (เพื่อให้เรารู้ว่ามันจับโดนอะไร)
        cv2.rectangle(frame, (rx, ry), (rx + rw, ry + rh), (0, 255, 255), 2)
        cv2.putText(frame, "Motion Detected", (rx, ry - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)

    if motion_detected and telegram_settings["enabled"]:
        current_time = time.time()
        if current_time - last_alert_time > telegram_settings["cooldown"]:
            msg = "🚨 ตรวจพบวัตถุเคลื่อนไหวหน้ากล้อง (Motion Detected)"
            threading.Thread(target=send_telegram_alert, args=(frame.copy(), msg)).start()
            last_alert_time = current_time

    return frame, gray

# ==========================================
# PROCESS FACE DETECTION IN FRAME
# ==========================================
def process_face_recognition(frame):
    global last_alert_time, telegram_settings
    
    if not face_rec_available:
        return frame
        
    small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
    rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
    
    face_locations = face_recognition.face_locations(rgb_small_frame)
    face_encodings = face_recognition.face_encodings(rgb_small_frame, face_locations)
    
    faces_found = []
    
    for (top, right, bottom, left), face_encoding in zip(face_locations, face_encodings):
        top *= 4
        right *= 4
        bottom *= 4
        left *= 4
        
        name = "Unknown"
        
        if len(known_face_encodings) > 0:
            matches = face_recognition.compare_faces(known_face_encodings, face_encoding)
            face_distances = face_recognition.face_distance(known_face_encodings, face_encoding)
            best_match_index = np.argmin(face_distances)
            if matches[best_match_index]:
                name = known_face_names[best_match_index]
        
        faces_found.append(name)
        
        cv2.rectangle(frame, (left, top), (right, bottom), (0, 255, 0) if name != "Unknown" else (0, 0, 255), 2)
        cv2.rectangle(frame, (left, bottom - 35), (right, bottom), (0, 255, 0) if name != "Unknown" else (0, 0, 255), cv2.FILLED)
        font = cv2.FONT_HERSHEY_DUPLEX
        cv2.putText(frame, name, (left + 6, bottom - 6), font, 0.8, (255, 255, 255), 1)
        
    if len(faces_found) > 0 and telegram_settings["enabled"]:
        current_time = time.time()
        if current_time - last_alert_time > telegram_settings["cooldown"]:
            names_msg = ", ".join(faces_found)
            msg = f"👤 ตรวจพบใบหน้าบุคคล: {names_msg}"
            threading.Thread(target=send_telegram_alert, args=(frame.copy(), msg)).start()
            last_alert_time = current_time

    return frame

# ==========================================
# VIDEO STREAMING LOGIC
# ==========================================

def generate_frames(rtsp_url):
    import os
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;udp|timeout;5000000"
    camera = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    if not camera.isOpened():
        return

    previous_frame = None
    try:
        while True:
            success, frame = camera.read()
            if not success:
                time.sleep(0.1) 
                continue
            
            frame = cv2.resize(frame, (800, 450)) 
            frame, previous_frame = process_motion_detection(frame, previous_frame)
            frame = process_face_recognition(frame)
            
            ret, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if not ret: continue
            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
    except Exception: pass
    finally: camera.release()

@app.route('/video_feed')
def video_feed():
    ip = request.args.get('ip')
    user = request.args.get('user', 'admin') 
    pwd = request.args.get('pwd')
    path = request.args.get('path', 'onvif1') 
    port = request.args.get('port', 554)
    if pwd:
        rtsp_url = f"rtsp://{user}:{pwd}@{ip}:{port}/{path}"
    else:
        rtsp_url = f"rtsp://{ip}:{port}/{path}"
    return Response(generate_frames(rtsp_url), mimetype='multipart/x-mixed-replace; boundary=frame')

def generate_pi_frames():
    camera_pi = cv2.VideoCapture(0)
    if not camera_pi.isOpened(): return
    
    previous_frame = None
    try:
        while True:
            success, frame = camera_pi.read()
            if not success:
                time.sleep(0.1)
                continue
                
            frame = cv2.resize(frame, (800, 450))
            frame, previous_frame = process_motion_detection(frame, previous_frame)
            frame = process_face_recognition(frame)
            
            ret, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if not ret: continue
            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
    except Exception: pass
    finally: camera_pi.release()

@app.route('/video_pi')
def video_pi():
    return Response(generate_pi_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

# ==========================================
# SYSTEM STATUS & SERVO API
# ==========================================

@app.route('/api/system_stats')
def system_stats():
    cpu_usage = psutil.cpu_percent(interval=0.1)
    ram_info = psutil.virtual_memory()
    disk_info = psutil.disk_usage('/')
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            temp_c = float(f.read()) / 1000.0
    except:
        temp_c = 0.0 
    return jsonify({
        "status": "ok", "cpu_percent": cpu_usage, "ram_percent": ram_info.percent,
        "ram_used_mb": round(ram_info.used / (1024 * 1024), 2),
        "ram_total_mb": round(ram_info.total / (1024 * 1024), 2),
        "disk_percent": disk_info.percent, "temperature_c": round(temp_c, 1)
    })

@app.route('/api/setup_servo', methods=['POST'])
def setup_servo():
    global servos_config
    try:
        data = request.get_json()
        servo_id = str(data.get('servo_id', '1'))
        conn_type = data.get('type', 'gpio') 
        pin = int(data.get('pin'))
        
        if servo_id not in servos_config:
            return jsonify({"status": "error", "message": "ไม่พบ Servo ID ในระบบ"}), 400
            
        if servos_config[servo_id]["type"] == "gpio" and servos_config[servo_id]["obj"] is not None:
            servos_config[servo_id]["obj"].close()
            servos_config[servo_id]["obj"] = None

        servos_config[servo_id]["type"] = conn_type
        servos_config[servo_id]["pin"] = pin
        servos_config[servo_id]["angle"] = 90
        
        if conn_type == "pca9685":
            if not pca_available:
                return jsonify({"status": "error", "message": "บอร์ด PCA9685 ไม่พร้อมทำงาน หรือยังไม่ได้เปิด I2C"}), 400
            pca_kit.servo[pin].angle = 90
            servos_config[servo_id]["connected"] = True
        elif conn_type == "raspbot":
            if not raspbot_available:
                return jsonify({"status": "error", "message": "ไลบรารี Raspbot ไม่พร้อมทำงาน"}), 400
            raspbot_car.Ctrl_Servo(pin, 90)
            servos_config[servo_id]["connected"] = True
        elif conn_type == "gpio":
            servos_config[servo_id]["obj"] = AngularServo(pin, min_angle=0, max_angle=180, pin_factory=factory)
            servos_config[servo_id]["obj"].angle = 90
            servos_config[servo_id]["connected"] = True

        save_servo_config()
        return jsonify({"status": "ok", "message": f"ตั้งค่าแบบ {conn_type.upper()} พอร์ต/ขา {pin} สำเร็จและบันทึกแล้ว", "current_angle": 90, "servo_id": servo_id})
    except Exception as e:
        return jsonify({"status": "error", "message": f"ไม่สามารถตั้งค่า Servo ได้: {str(e)}"}), 400

@app.route('/move', methods=['POST'])
def move():
    global servos_config
    try:
        data = request.get_json()
        servo_id = str(data.get('servo_id', '1'))
        direction = data.get('direction')
        step = 5 

        if servo_id not in servos_config or not servos_config[servo_id]["connected"]:
            return jsonify({"status": "error", "message": f"ไม่ได้เชื่อมต่อ Servo {servo_id}"}), 400
            
        current_angle = servos_config[servo_id]["angle"]
        conn_type = servos_config[servo_id]["type"]
        pin = servos_config[servo_id]["pin"]

        if direction == 'left' or direction == 'down':
            current_angle = max(0, current_angle - step)
        elif direction == 'right' or direction == 'up':
            current_angle = min(180, current_angle + step)
        elif direction == 'home':
            current_angle = 90

        servos_config[servo_id]["angle"] = current_angle
        
        if conn_type == 'pca9685':
            pca_kit.servo[pin].angle = current_angle
        elif conn_type == 'raspbot':
            raspbot_car.Ctrl_Servo(pin, current_angle)
        elif conn_type == 'gpio':
            target_servo = servos_config[servo_id]["obj"]
            target_servo.angle = current_angle
            time.sleep(0.15)
            target_servo.detach() 
            
        return jsonify({"status": "ok", "angle": current_angle, "pin": pin, "type": conn_type, "servo_id": servo_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/get_servos_status')
def get_servos_status():
    global servos_config
    return jsonify({
        "servo1_connected": servos_config["1"]["connected"],
        "servo1_angle": servos_config["1"]["angle"],
        "servo1_type": servos_config["1"]["type"],
        "servo1_pin": servos_config["1"]["pin"],
        
        "servo2_connected": servos_config["2"]["connected"],
        "servo2_angle": servos_config["2"]["angle"],
        "servo2_type": servos_config["2"]["type"],
        "servo2_pin": servos_config["2"]["pin"]
    })

# ==========================================
# FACE & TELEGRAM REGISTRATION API
# ==========================================

@app.route('/api/face/register', methods=['POST'])
def register_face():
    if 'image' not in request.files or 'name' not in request.form:
        return jsonify({"status": "error", "message": "ข้อมูลไม่ครบถ้วน (ต้องส่ง image และ name มาด้วย)"}), 400
        
    file = request.files['image']
    name = request.form['name']
    
    if file.filename == '':
        return jsonify({"status": "error", "message": "ไม่ได้เลือกไฟล์ภาพ"}), 400
        
    filename = f"{name.replace(' ', '_')}_{int(time.time())}.jpg"
    filepath = os.path.join(FACE_DIR, filename)
    
    try:
        file.save(filepath)
        
        data = []
        if os.path.exists(FACE_CONFIG):
            with open(FACE_CONFIG, 'r', encoding='utf-8') as f:
                data = json.load(f)
        
        data.append({
            "name": name,
            "image_path": filepath,
            "timestamp": int(time.time())
        })
        
        with open(FACE_CONFIG, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
            
        load_known_faces()
        
        return jsonify({"status": "ok", "message": f"บันทึกและตั้งชื่อใบหน้าเป็น {name} สำเร็จ", "file": filename})
        
    except Exception as e:
        return jsonify({"status": "error", "message": f"เกิดข้อผิดพลาดในการบันทึกภาพ: {str(e)}"}), 500

@app.route('/api/face/list', methods=['GET'])
def list_faces():
    if os.path.exists(FACE_CONFIG):
        try:
            with open(FACE_CONFIG, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return jsonify({"status": "ok", "faces": data, "total": len(data)})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
            
    return jsonify({"status": "ok", "faces": [], "total": 0})

@app.route('/api/telegram/setup', methods=['POST'])
def setup_telegram():
    global telegram_settings
    data = request.get_json()
    
    telegram_settings["bot_token"] = data.get("bot_token", telegram_settings["bot_token"])
    telegram_settings["chat_id"] = data.get("chat_id", telegram_settings["chat_id"])
    telegram_settings["enabled"] = data.get("enabled", telegram_settings["enabled"])
    telegram_settings["cooldown"] = data.get("cooldown", telegram_settings["cooldown"])
    
    save_telegram_config()
    status_msg = "เปิด" if telegram_settings["enabled"] else "ปิด"
    return jsonify({"status": "ok", "message": f"บันทึกการตั้งค่า Telegram สำเร็จ (สถานะ: {status_msg})"})

@app.route('/api/telegram/status', methods=['GET'])
def get_telegram_status():
    return jsonify(telegram_settings)

@app.route('/api/telegram/get_chats', methods=['POST'])
def get_telegram_chats():
    data = request.get_json()
    bot_token = data.get("bot_token")
    
    if not bot_token:
        return jsonify({"status": "error", "message": "กรุณาระบุ Bot Token ให้เรียบร้อยก่อน"})
        
    if not requests_available:
        return jsonify({"status": "error", "message": "ไลบรารี requests ไม่พร้อมทำงาน"})
        
    try:
        url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
        response = requests.get(url, timeout=10)
        result = response.json()
        
        if not result.get("ok"):
            return jsonify({"status": "error", "message": "Bot Token ไม่ถูกต้อง หรือไม่สามารถเชื่อมต่อกับ Telegram ได้"})
            
        chats = {}
        for item in result.get("result", []):
            if "message" in item and "chat" in item["message"]:
                chat = item["message"]["chat"]
                chat_id = chat.get("id")
                chat_type = chat.get("type")
                
                name = ""
                if chat_type == "private":
                    name = chat.get("first_name", "") + " " + chat.get("last_name", "")
                    name = name.strip()
                    if not name:
                        name = chat.get("username", "Unknown User")
                    name = f"👤 {name} (ส่วนตัว)"
                else:
                    name = f"👥 {chat.get('title', 'Unknown Group')} (กลุ่ม)"
                    
                chats[str(chat_id)] = {"id": str(chat_id), "name": name}
                
        chat_list = list(chats.values())
        if not chat_list:
            return jsonify({"status": "error", "message": "ไม่พบประวัติการสนทนา (อย่าลืมทัก /start หาบอท หรือดึงบอทเข้ากลุ่มก่อนกดค้นหานะครับ)"})
            
        return jsonify({"status": "ok", "chats": chat_list})
        
    except Exception as e:
        return jsonify({"status": "error", "message": f"เกิดข้อผิดพลาดในการดึงข้อมูล: {str(e)}"})

# ==========================================
# FLASK ROUTES
# ==========================================
@app.route('/')
def index():
    local_ip = get_local_ip()
    subnet = '.'.join(local_ip.split('.')[:-1]) + '.'
    return render_template('index.html', local_ip=local_ip, subnet=subnet)

@app.route('/live')
def index_live(): return render_template('live.html')

@app.route('/camera')
def index_camera(): return render_template('camera.html')

@app.route('/servo')
def index_servo(): return render_template('servo.html')

@app.route('/control')
def index_control(): return render_template('control.html')

@app.route('/scan_network', methods=['POST'])
def scan_network():
    data = request.json
    subnet = data.get('subnet')
    if not subnet.endswith('.'): subnet += '.'
    with ThreadPoolExecutor(max_workers=100) as executor:
        ips = [f"{subnet}{i}" for i in range(1, 255)]
        results = list(filter(None, executor.map(quick_scan_host, ips)))
    results.sort(key=lambda x: int(x['ip'].split('.')[-1]))
    return jsonify({'results': results})

@app.route('/deep_scan', methods=['POST'])
def deep_scan():
    target_ip = request.json.get('ip')
    open_ports = []
    with ThreadPoolExecutor(max_workers=200) as executor:
        futures = {executor.submit(check_port, target_ip, p, 0.05): p for p in range(1, 65536)}
        from concurrent.futures import as_completed
        for future in as_completed(futures):
            res = future.result()
            if res: open_ports.append(res)
    return jsonify({'ip': target_ip, 'open_ports': sorted(open_ports)})

def cleanup():
    global servos_config
    for sid, sdata in servos_config.items():
        if sdata["type"] == "gpio" and sdata["obj"] is not None:
            try:
                sdata["obj"].angle = 0
                sleep(0.2)
                sdata["obj"].value = None
                sdata["obj"].close()
            except: pass

if __name__ == '__main__':
    try:
        load_servo_config()
        load_telegram_config()
        load_known_faces()
        check_network_and_fallback()
        app.run(host='0.0.0.0', port=8000, debug=False, threaded=True)
    except KeyboardInterrupt:
        cleanup()
    finally:
        cleanup()
