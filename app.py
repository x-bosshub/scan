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

# ตัวแปร Global สำหรับ Servo
servo = None
servo_pin = None
state = {"angle": 90}

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
        # Regex หา MAC Address ที่ตรงกับ IP
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
    
    # 1. CCTV / DVR / NVR (Priority สูงสุด)
    if 37777 in ports: return "Dahua Device", "fa-video", "bg-camera"
    if 34567 in ports: return "XMeye/China DVR", "fa-video", "bg-camera"
    if 8200 in ports: return "Hikvision Device", "fa-video", "bg-camera"
    if 554 in ports or 1935 in ports: return "IP Camera/NVR", "fa-video", "bg-camera"
    
    # 2. IoT & Infrastructure
    if 1883 in ports: return "MQTT Broker/IoT", "fa-microchip", "bg-iot"
    if 3306 in ports or 5432 in ports: return "Database Server", "fa-database", "bg-warning text-dark"
    if 9000 in ports: return "Portainer/Docker", "fa-docker", "bg-info text-dark"

    # 3. Server / OS
    if 22 in ports and ("Raspberry" in vendor or "Linux" in vendor): return "Linux Server", "fa-server", "bg-server"
    if 111 in ports: return "Unix/Linux Device", "fa-linux", "bg-server"

    # 4. Web Application
    if any(p in ports for p in [80, 443,8000, 4000,8080, 3000, 5000,6080,7681]): return "Web Server/App", "fa-globe", "bg-light text-dark"

    # 5. Fallback by Vendor
    if "Espressif" in vendor: return "ESP32 Device", "fa-microchip", "bg-iot"
    if "Apple" in vendor: return "Apple Device", "fa-apple", "bg-light text-dark"
    if "Synology" in vendor: return "NAS Storage", "fa-hdd", "bg-secondary text-white"

    return "Network Device", "fa-network-wired", "bg-light text-dark"

def check_port(ip, port, timeout=0.0):
    """ตรวจสอบสถานะพอร์ต (Open/Closed) - ปรับ timeout ให้สั้นลง"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        res = sock.connect_ex((ip, port))
        sock.close()
        return port if res == 0 else None
    except:
        sock.close() # Ensure close on error
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
    port = request.args.get('port',554)

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
    
    # อ่านค่าความร้อนจาก Raspberry Pi
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            temp_c = float(f.read()) / 1000.0
    except:
        temp_c = 0.0 # กรณีไม่ได้รันบนบอร์ดที่รองรับเซ็นเซอร์นี้
        
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
    global servo, servo_pin, state
    try:
        data = request.get_json()
        pin = int(data.get('pin'))
        
        # ปิดการเชื่อมต่อขาเก่า (ถ้ามี)
        if servo is not None:
            servo.close()
            
        # สร้างออบเจ็กต์ Servo ใหม่ โดยกำหนดองศาเป็น 0 ถึง 180 ตามตรรกะเดิมของคุณ
        servo = AngularServo(pin, min_angle=0, max_angle=180, pin_factory=factory)
        servo_pin = pin
        state["angle"] = 90 # เริ่มต้นที่กึ่งกลาง
        
        return jsonify({"status": "ok", "message": f"ตั้งค่า Servo ที่ขา GPIO {pin} สำเร็จ", "current_angle": state["angle"]})
    except Exception as e:
        return jsonify({"status": "error", "message": f"ไม่สามารถตั้งค่า GPIO ได้: {str(e)}"}), 400

# ==========================================
# FLASK ROUTES
# ==========================================

@app.route('/')
def index():
    local_ip = get_local_ip()
    subnet = '.'.join(local_ip.split('.')[:-1]) + '.'
    return render_template('index.html', local_ip=local_ip, subnet=subnet)

@app.route('/live')
def index_live():
    return render_template('live.html')

@app.route('/scan_network', methods=['POST'])
def scan_network():
    """API: สแกนอุปกรณ์ในวงแลน (Quick Scan)"""
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
    """API: สแกน 65,535 Ports (Deep Scan)"""
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
    """Route สำหรับ <img> tag เพื่อแสดงภาพสด"""
    ip = request.args.get('ip')
    user = request.args.get('user', '')
    pwd = request.args.get('pwd', '')
    path = request.args.get('path', 'onvif1')
    
    if user and pwd:
        rtsp_url = f"rtsp://{user}:{pwd}@{ip}:554/{path}"
    else:
        rtsp_url = f"rtsp://{ip}:554/{path}"
        
    print(f"Streaming from: {rtsp_url}")
    return Response(generate_frames(rtsp_url), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/servo')
def index_servo():
    return render_template('servo.html')

@app.route('/move', methods=['POST'])
def move():
    global servo, state
    
    # เช็คว่าได้ตั้งค่าขา GPIO หรือยัง
    if servo is None:
        return jsonify({"status": "error", "message": "กรุณาตั้งค่าขา GPIO (Setup Servo) ก่อนสั่งงาน"}), 400
        
    try:
        data = request.get_json()
        direction = data.get('direction')
        step = 5 # ปรับความละเอียดตรงนี้ (ขยับทีละ 5 องศา)

        if direction == 'left':
            state["angle"] = max(0, state["angle"] - step)
        elif direction == 'right':
            state["angle"] = min(180, state["angle"] + step)

        # สั่งงาน Servo
        servo.angle = state["angle"]
        
        # รอให้มอเตอร์เคลื่อนที่ครู่หนึ่ง
        time.sleep(0.15)
        
        # ตัดสัญญาณทันทีเพื่อป้องกันการหมุนไม่หยุด (Continuous Rotation Fix)
        servo.detach() 
        
        return jsonify({"status": "ok", "angle": state["angle"], "pin": servo_pin})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

# ล้างค่า GPIO เมื่อปิดโปรแกรม
def cleanup():
    global servo
    if servo:
        servo.angle = 0
        sleep(0.2)
        servo.value = None

if __name__ == '__main__':
    try:
        app.run(host='0.0.0.0', port=8000, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("close")
    finally:
        print("close")
