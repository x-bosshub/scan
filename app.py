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
    """เปิดโหมด Access Point (Hotspot) ผ่าน nmcli แบบกำหนดเองเพื่อป้องกันปัญหา DHCP"""
    print("[SYSTEM] เครือข่ายไม่มีการเชื่อมต่อ กำลังเปิดโหมด AP (Hotspot)...")
    try:
        # ตัดการเชื่อมต่อเดิมที่อาจค้างอยู่
        subprocess.run(['sudo', 'nmcli', 'device', 'disconnect', 'wlan0'], capture_output=True)
        
        # ลบโปรไฟล์ Hotspot ทั้งชื่อเก่าและใหม่ (ถ้ามี)
        subprocess.run(['sudo', 'nmcli', 'connection', 'delete', 'Pi5_Hotspot'], capture_output=True)
        subprocess.run(['sudo', 'nmcli', 'connection', 'delete', 'Hotspot'], capture_output=True)
        
        # หน่วงเวลาให้ NetworkManager เคลียร์สถานะ Interface ให้สมบูรณ์ ป้องกัน Race condition
        time.sleep(2)
        
        # สร้าง Profile ใหม่แบบเจาะจง ให้แชร์เน็ตเวิร์คและแจก IP (ipv4.method shared)
        subprocess.run(['sudo', 'nmcli', 'connection', 'add', 'type', 'wifi', 'ifname', 'wlan0', 'con-name', 'Pi5_Hotspot', 'autoconnect', 'yes', 'ssid', 'Pi5_Setup'], capture_output=True)
        subprocess.run(['sudo', 'nmcli', 'connection', 'modify', 'Pi5_Hotspot', '802-11-wireless.mode', 'ap', '802-11-wireless.band', 'bg', 'ipv4.method', 'shared'], capture_output=True)
        subprocess.run(['sudo', 'nmcli', 'connection', 'modify', 'Pi5_Hotspot', 'wifi-sec.key-mgmt', 'wpa-psk', 'wifi-sec.psk', '12345678'], capture_output=True)
        
        # สั่งเปิดการเชื่อมต่อ
        res = subprocess.run(['sudo', 'nmcli', 'connection', 'up', 'Pi5_Hotspot'], capture_output=True, text=True)
        
        if res.returncode == 0:
            print("[SYSTEM] เปิด AP Mode สำเร็จ: SSID=Pi5_Setup, Password=12345678")
        else:
            print(f"[ERROR] ไม่สามารถเปิด AP Mode ได้: {res.stderr}")
    except Exception as e:
        print(f"[ERROR] เกิดข้อผิดพลาดขณะเปิด AP Mode: {e}")

def check_network_and_fallback():
    """ตรวจสอบการเชื่อมต่อเครือข่าย พยายามสแกนและเชื่อมต่อ Wi-Fi เดิมก่อน หากไม่ได้ให้เปิด AP Mode"""
    print("[SYSTEM] กำลังตรวจสอบสถานะเครือข่าย...")
    
    # รอบแรก: ลองเช็คดูว่าได้ IP หรือยัง (กรณีต่อสาย LAN หรือ Wi-Fi เกาะอัตโนมัติสำเร็จแล้ว)
    ip = get_local_ip()
    if ip != '127.0.0.1':
        print(f"[SYSTEM] พบการเชื่อมต่อเครือข่ายสำเร็จ IP ปัจจุบัน: {ip}")
        return

    # กรณีไม่ได้ IP: พยายามเปิดใช้งาน wlan0 และสั่งให้ NetworkManager ค้นหา/เชื่อมต่อโปรไฟล์เดิมที่มีอยู่
    print("[SYSTEM] ไม่พบ IP, กำลังพยายามเปิดการค้นหา Wi-Fi และเชื่อมต่ออัตโนมัติ...")
    try:
        subprocess.run(['sudo', 'nmcli', 'radio', 'wifi', 'on'], capture_output=True)
        time.sleep(1)
        # สั่งสแกนหนึ่งรอบเพื่อกระตุ้นให้บอร์ดเห็นเราเตอร์รอบตัว
        subprocess.run(['sudo', 'nmcli', 'dev', 'wifi', 'rescan'], capture_output=True)
        time.sleep(4) # รอให้ระบบคุยกับ Access Point เดิมสักครู่
    except Exception as e:
        print(f"[WARNING] ไม่สามารถสั่ง rescan wifi ได้: {e}")

    # เช็ค IP อีกครั้งหลังจากสั่งสแกน/กระตุ้นการเชื่อมต่อ
    ip = get_local_ip()
    if ip == '127.0.0.1':
        print("[SYSTEM] ไม่สามารถเชื่อมต่อ Wi-Fi ที่บันทึกไว้ได้")
        enable_ap_mode()
    else:
        print(f"[SYSTEM] เชื่อมต่อ Wi-Fi เดิมสำเร็จ IP ปัจจุบัน: {ip}")

@app.route('/api/wifi/scan')
def wifi_scan():
    """ใช้ nmcli สแกนหา Wi-Fi รอบๆ เครื่อง (เพิ่ม Timeout และ Rescan)"""
    try:
        # สั่งกระตุ้นการสแกนรอบใหม่ก่อน (เผื่อบอร์ดหลับหรือได้ข้อมูลเก่า)
        try:
            subprocess.run(['sudo', 'nmcli', 'dev', 'wifi', 'rescan'], timeout=5, capture_output=True)
            time.sleep(2) # หน่วงเวลารอรับผลสแกนใหม่
        except subprocess.TimeoutExpired:
            pass # ถ้า rescan timeout ช่างมัน ให้ไปพยายามดึงข้อมูลแทน
            
        # ดึงข้อมูลรูปแบบ SSID:SIGNAL:SECURITY (เพิ่ม sudo และขยายเวลา timeout เป็น 20 วินาที)
        res = subprocess.check_output(['sudo', 'nmcli', '-t', '-f', 'SSID,SIGNAL,SECURITY', 'dev', 'wifi'], timeout=20).decode('utf-8')
        networks = []
        for line in res.split('\n'):
            if line.strip():
                parts = line.split(':')
                if len(parts) >= 3 and parts[0]: # ต้องมีชื่อ SSID
                    networks.append({'ssid': parts[0], 'signal': int(parts[1]), 'security': parts[2]})
        
        # กรองเอาเฉพาะชื่อที่ไม่ซ้ำ (เอาสัญญาณแรงสุด)
        unique_nets = {}
        for n in networks:
            if n['ssid'] not in unique_nets or n['signal'] > unique_nets[n['ssid']]['signal']:
                unique_nets[n['ssid']] = n
                
        return jsonify({"status": "ok", "networks": list(unique_nets.values())})
        
    except subprocess.TimeoutExpired:
        # ดักจับ Timeout และส่งข้อความภาษาไทยแจ้งผู้ใช้งาน
        return jsonify({"status": "error", "message": "หมดเวลาในการสแกน (Timeout) โมดูล Wi-Fi อาจกำลังยุ่ง กรุณากด Rescan อีกครั้ง", "networks": []}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "networks": []}), 500

@app.route('/api/wifi/connect', methods=['POST'])
def wifi_connect():
    """สั่งเชื่อมต่อ Wi-Fi ด้วย nmcli"""
    data = request.get_json()
    ssid = data.get('ssid')
    password = data.get('password')
    try:
        # ปิดและลบโหมด Hotspot ก่อนจะเชื่อมต่อ Wi-Fi ปลายทาง เพื่อป้องกันการชนกันของ Interface
        subprocess.run(['sudo', 'nmcli', 'connection', 'down', 'Pi5_Hotspot'], capture_output=True)
        subprocess.run(['sudo', 'nmcli', 'connection', 'delete', 'Pi5_Hotspot'], capture_output=True)
        subprocess.run(['sudo', 'nmcli', 'connection', 'down', 'Hotspot'], capture_output=True)
        subprocess.run(['sudo', 'nmcli', 'connection', 'delete', 'Hotspot'], capture_output=True)
        
        # หน่วงเวลาให้ wlan0 ว่างพร้อมเชื่อมต่อ
        time.sleep(2)
        
        if password:
            result = subprocess.run(['sudo', 'nmcli', 'dev', 'wifi', 'connect', ssid, 'password', password], capture_output=True, text=True, timeout=20)
        else:
            result = subprocess.run(['sudo', 'nmcli', 'dev', 'wifi', 'connect', ssid], capture_output=True, text=True, timeout=20)
            
        if result.returncode == 0:
            return jsonify({"status": "ok", "message": f"เชื่อมต่อ {ssid} สำเร็จ"})
        else:
            # ถ้าเชื่อมต่อไม่สำเร็จ ให้เช็คและกลับไปเปิด AP อีกรอบ
            check_network_and_fallback()
            return jsonify({"status": "error", "message": result.stderr})
    except Exception as e:
        check_network_and_fallback()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/bt/scan')
def bt_scan():
    """สแกน Bluetooth ใช้ bluetoothctl"""
    try:
        # เปิดสแกนทิ้งไว้ 5 วินาที
        subprocess.run(['bluetoothctl', '--timeout', '5', 'scan', 'on'], capture_output=True)
        # ดึงรายชื่ออุปกรณ์
        res = subprocess.check_output(['bluetoothctl', 'devices']).decode('utf-8')
        devices = []
        for line in res.split('\n'):
            if line.startswith('Device '):
                parts = line.split(' ', 2)
                if len(parts) >= 3:
                    mac = parts[1]
                    name = parts[2]
                    # กรองพวกที่ชื่อเป็น MAC ล้วนๆ ออกให้ดูสะอาดขึ้น
                    if '-' not in name: 
                        devices.append({"mac": mac, "name": name})
        return jsonify({"status": "ok", "devices": devices})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "devices": []}), 500

@app.route('/api/bt/connect', methods=['POST'])
def bt_connect():
    """จับคู่และเชื่อมต่อ Bluetooth"""
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
# VIDEO STREAMING LOGIC
# ==========================================

def generate_frames(rtsp_url):
    import os
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;udp|timeout;5000000"
    camera = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    if not camera.isOpened():
        return

    try:
        while True:
            success, frame = camera.read()
            if not success:
                time.sleep(0.1) 
                continue
            frame = cv2.resize(frame, (800, 450)) 
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
    try:
        while True:
            success, frame = camera_pi.read()
            if not success:
                time.sleep(0.1)
                continue
            frame = cv2.resize(frame, (800, 450))
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
        elif conn_type == "gpio":
            servos_config[servo_id]["obj"] = AngularServo(pin, min_angle=0, max_angle=180, pin_factory=factory)
            servos_config[servo_id]["obj"].angle = 90
            servos_config[servo_id]["connected"] = True

        save_servo_config()
        return jsonify({"status": "ok", "message": f"ตั้งค่าแบบ {conn_type.upper()} ขา {pin} สำเร็จและบันทึกแล้ว", "current_angle": 90, "servo_id": servo_id})
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
        # ตรวจสอบเครือข่ายก่อนรัน Flask Server (พยายามสแกนเกาะวงเดิมก่อน Fallback)
        check_network_and_fallback()
        app.run(host='0.0.0.0', port=8000, debug=False, threaded=True)
    except KeyboardInterrupt:
        cleanup()
    finally:
        cleanup()
