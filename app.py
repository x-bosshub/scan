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
from gpiozero import AngularServo
from gpiozero.pins.lgpio import LGPIOFactory
from time import sleep

factory = LGPIOFactory()

app = Flask(__name__) 

# ตัวแปร Global สำหรับจัดการ Servo 2 ตัว (1=ซ้ายขวา, 2=ขึ้นลง)
servos_config = {
    "1": {"obj": None, "pin": None, "angle": 90, "name": "ซ้าย - ขวา (Pan)"},
    "2": {"obj": None, "pin": None, "angle": 90, "name": "ขึ้น - ลง (Tilt)"}
}

# 1. Quick Scan Ports: รายการพอร์ตสำหรับสแกนเร็วเพื่อระบุประเภทอุปกรณ์
COMMON_PORTS = [
    21, 22, 23,43, 53, 80, 81, 111, 443,
    554, 1935,
    1883, 8883,
    3306, 5432, 27017,                        # Database (MySQL, Postgres, Mongo)
    3000, 4000, 5000, 6080, 7681, 7000,       # Web Apps / Dev
    8000, 8008, 8009, 8080, 8081, 8090, 8092, # Web Alternatives
    8200, 8443, 8899, 9000, 9080,             # Admin / Docker / Portainer
    34567, 37777, 37778,37779           # CCTV Specific (XMeye, Dahua)
]

# 2. Vendor Database: จับคู่ MAC Address กับผู้ผลิต
MAC_VENDORS = {
    "DC:A6:32": "Raspberry Pi", "B8:27:EB": "Raspberry Pi", "E4:5F:01": "Raspberry Pi", "28:CD:C1": "Raspberry Pi",
    "D8:3A:DD": "Espressif", "24:0A:C4": "Espressif", "30:AE:A4": "Espressif", "AC:67:B2": "Espressif", "60:01:94": "Espressif",
    "F4:F5:DB": "Apple", "AE:60:5C": "Apple",
    "00:11:32": "Synology", "00:E0:4C": "Realtek",
    "48:EA:63": "Dahua", "E0:50:8B": "Hikvision", "10:12:48": "Hikvision",
    "00:0C:29": "VMware", "00:50:56": "VMware"
}

# ==========================================
# CORE FUNCTIONS
# ==========================================

def get_local_ip():
    """หา IP ของเครื่องที่รันโปรแกรม"""
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
    """อ่าน MAC Address จาก ARP Cache ของ Linux/Pi"""
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
    """แปลง MAC OUI เป็นชื่อผู้ผลิต"""
    if not mac: return ""
    for oui, vendor in MAC_VENDORS.items():
        if mac.startswith(oui): return vendor
    return ""

def identify_device_type(ports, vendor):
    """สมองกล: วิเคราะห์ประเภทอุปกรณ์จาก Port และ Vendor"""
    
    if 37777 in ports: return "Dahua Device", "fa-video", "bg-camera"
    if 34567 in ports: return "XMeye/China DVR", "fa-video", "bg-camera"
    if 8200 in ports: return "Hikvision Device", "fa-video", "bg-camera"
    if 554 in ports or 1935 in ports: return "IP Camera/NVR", "fa-video", "bg-camera"
    
    if 1883 in ports: return "MQTT Broker/IoT", "fa-microchip", "bg-iot"
    if 3306 in ports or 5432 in ports: return "Database Server", "fa-database", "bg-warning text-dark"
    if 9000 in ports: return "Portainer/Docker", "fa-docker", "bg-info text-dark"

    if 22 in ports and ("Raspberry" in vendor or "Linux" in vendor): return "Linux Server", "fa-server", "bg-server"
    if 111 in ports: return "Unix/Linux Device", "fa-linux", "bg-server"

    if any(p in ports for p in [80, 443,8000, 4000,8080, 3000, 5000,6080,7681]): return "Web Server/App", "fa-globe", "bg-light text-dark"

    if "Espressif" in vendor: return "ESP32 Device", "fa-microchip", "bg-iot"
    if "Apple" in vendor: return "Apple Device", "fa-apple", "bg-light text-dark"
    if "Synology" in vendor: return "NAS Storage", "fa-hdd", "bg-secondary text-white"

    return "Network Device", "fa-network-wired", "bg-light text-dark"

def check_port(ip, port, timeout=0.0):
    """ตรวจสอบสถานะพอร์ต (Open/Closed)"""
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
    """สแกน 1 เครื่องแบบรวดเร็ว (Quick Scan)"""
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
# PROFESSIONAL VIDEO STREAMING LOGIC
# ==========================================

def generate_frames(rtsp_url):
    """ดึงภาพจาก RTSP พร้อมการตั้งค่า FFMPEG เพื่อความเสถียรสูงสุด"""
    import os
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;udp|timeout;5000000"

    camera = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    if not camera.isOpened():
        print(f"[ERROR] ไม่สามารถเชื่อมต่อกล้องได้: {rtsp_url}")
        return

    try:
        while True:
            success, frame = camera.read()
            if not success:
                time.sleep(0.1) 
                continue

            frame = cv2.resize(frame, (800, 450)) 

            ret, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if not ret:
                continue
                
            frame_bytes = buffer.tobytes()
            
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                   
    except Exception as e:
        print(f"[SYSTEM] Stream error: {e}")
    finally:
        camera.release()

@app.route('/video_feed')
def video_feed():
    """Route สำหรับดึงภาพสดไปแสดงบนหน้าเว็บ"""
    ip = request.args.get('ip')
    user = request.args.get('user', 'admin') 
    pwd = request.args.get('pwd')
    path = request.args.get('path', 'onvif1') 
    port = request.args.get('port', 554)

    if pwd:
        rtsp_url = f"rtsp://{user}:{pwd}@{ip}:{port}/{path}"
    else:
        rtsp_url = f"rtsp://{ip}:{port}/{path}"
        
    print(f"[LOG] เริ่มการสตรีมจาก: {rtsp_url}")
    
    return Response(
        generate_frames(rtsp_url),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

# ==========================================
# SYSTEM STATUS & HARDWARE LOGIC
# ==========================================

@app.route('/api/system_stats')
def system_stats():
    """API ดึงข้อมูลสถานะเครื่อง (CPU, RAM, Temp)"""
    cpu_usage = psutil.cpu_percent(interval=0.1)
    ram_info = psutil.virtual_memory()
    disk_info = psutil.disk_usage('/')
    
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            temp_c = float(f.read()) / 1000.0
    except:
        temp_c = 0.0 
        
    return jsonify({
        "status": "ok",
        "cpu_percent": cpu_usage,
        "ram_percent": ram_info.percent,
        "ram_used_mb": round(ram_info.used / (1024 * 1024), 2),
        "ram_total_mb": round(ram_info.total / (1024 * 1024), 2),
        "disk_percent": disk_info.percent,
        "temperature_c": round(temp_c, 1)
    })

@app.route('/api/setup_servo', methods=['POST'])
def setup_servo():
    """API ตั้งค่าหมายเลข GPIO ขาของ Servo"""
    global servos_config
    try:
        data = request.get_json()
        servo_id = str(data.get('servo_id', '1'))
        pin = int(data.get('pin'))
        
        if servo_id not in servos_config:
            return jsonify({"status": "error", "message": "ไม่พบ Servo ID ในระบบ"}), 400
            
        if servos_config[servo_id]["obj"] is not None:
            servos_config[servo_id]["obj"].close()
            
        servos_config[servo_id]["obj"] = AngularServo(pin, min_angle=0, max_angle=180, pin_factory=factory)
        servos_config[servo_id]["pin"] = pin
        servos_config[servo_id]["angle"] = 90 
        
        return jsonify({
            "status": "ok", 
            "message": "ตั้งค่าสำเร็จ", 
            "current_angle": servos_config[servo_id]["angle"],
            "servo_id": servo_id
        })
    except Exception as e:
        return jsonify({"status": "error", "message": f"ไม่สามารถตั้งค่า GPIO ได้: {str(e)}"}), 400

@app.route('/move', methods=['POST'])
def move():
    """API ควบคุม Servo ตาม ID ที่ระบุ"""
    global servos_config
    try:
        data = request.get_json()
        servo_id = str(data.get('servo_id', '1'))
        direction = data.get('direction')
        step = 5 

        if servo_id not in servos_config or servos_config[servo_id]["obj"] is None:
            return jsonify({"status": "error", "message": f"ไม่ได้ต่อสาย Servo {servo_id}"}), 400
            
        current_angle = servos_config[servo_id]["angle"]
        target_servo = servos_config[servo_id]["obj"]

        # รองรับซ้ายขวา (ID 1) และขึ้นลง (ID 2)
        if direction == 'left' or direction == 'down':
            current_angle = max(0, current_angle - step)
        elif direction == 'right' or direction == 'up':
            current_angle = min(180, current_angle + step)

        servos_config[servo_id]["angle"] = current_angle
        target_servo.angle = current_angle
        
        time.sleep(0.15)
        target_servo.detach() 
        
        return jsonify({
            "status": "ok", 
            "angle": current_angle, 
            "pin": servos_config[servo_id]["pin"],
            "servo_id": servo_id
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/get_servos_status')
def get_servos_status():
    """API ดึงสถานะปัจจุบันของเซอร์โวทั้ง 2 ตัวไปเช็คที่หน้ากล้อง"""
    global servos_config
    return jsonify({
        "servo1_connected": servos_config["1"]["obj"] is not None,
        "servo1_angle": servos_config["1"]["angle"],
        "servo2_connected": servos_config["2"]["obj"] is not None,
        "servo2_angle": servos_config["2"]["angle"]
    })

# ==========================================
# FLASK ROUTES
# ==========================================

@app.route('/')
def index():
    local_ip = get_local_ip()
    subnet = '.'.join(local_ip.split('.')[:-1]) + '.'
    return render_template('index.html', local_ip=local_ip, subnet=subnet)

@app.route('/camera')
def index_camera():
    """เปลี่ยนจากหน้า /live เดิม เป็นหน้าควบคุม /camera แบบ PTZ อัตโนมัติ"""
    return render_template('camera.html')

@app.route('/scan_network', methods=['POST'])
def scan_network():
    data = request.json
    subnet = data.get('subnet')
    
    if not subnet.endswith('.'):
        subnet += '.'

    print(f"Scanning subnet: {subnet}1 - {subnet}254")
    
    with ThreadPoolExecutor(max_workers=100) as executor:
        ips = [f"{subnet}{i}" for i in range(1, 255)]
        results = list(filter(None, executor.map(quick_scan_host, ips)))
    
    results.sort(key=lambda x: int(x['ip'].split('.')[-1]))
    return jsonify({'results': results})

@app.route('/deep_scan', methods=['POST'])
def deep_scan():
    target_ip = request.json.get('ip')
    print(f"Deep scanning target: {target_ip}")
    
    open_ports = []
    max_port = 65535
    
    with ThreadPoolExecutor(max_workers=200) as executor:
        futures = {executor.submit(check_port, target_ip, p, 0.05): p for p in range(1, max_port + 1)}
        
        from concurrent.futures import as_completed
        for future in as_completed(futures):
            res = future.result()
            if res:
                open_ports.append(res)
            
    return jsonify({'ip': target_ip, 'open_ports': sorted(open_ports)})

@app.route('/video_feedold')
def video_feed_old():
    ip = request.args.get('ip')
    user = request.args.get('user', '')
    pwd = request.args.get('pwd', '')
    path = request.args.get('path', 'onvif1')
    
    if user and pwd:
        rtsp_url = f"rtsp://{user}:{pwd}@{ip}:554/{path}"
    else:
        rtsp_url = f"rtsp://{ip}:554/{path}"
        
    return Response(generate_frames(rtsp_url), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/servo')
def index_servo():
    return render_template('servo.html')

def cleanup():
    global servos_config
    for sid, sdata in servos_config.items():
        if sdata["obj"] is not None:
            try:
                sdata["obj"].angle = 0
                sleep(0.2)
                sdata["obj"].value = None
                sdata["obj"].close()
            except:
                pass

if __name__ == '__main__':
    try:
        app.run(host='0.0.0.0', port=8000, debug=False, threaded=True)
    except KeyboardInterrupt:
        cleanup()
        print("close")
    finally:
        cleanup()
        print("close")
