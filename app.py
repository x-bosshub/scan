import socket
import re
import cv2
from flask import Flask, render_template, jsonify, request, Response
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import sys
from gpiozero import AngularServo
from gpiozero.pins.lgpio import LGPIOFactory
from time import sleep

factory = LGPIOFactory()

app = Flask(__name__) 

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
    
    # 1. Ping Check (Optional but Recommended for speed)
    # ถ้าไม่อยากใช้ Ping (เพราะต้อง run sudo) ให้ใช้วิธีเช็ค port เร็วๆ แทน
    # ปรับ timeout ให้สั้นมาก (0.05s) เพื่อให้ข้าม Dead IP ได้เร็ว
    scan_timeout = 0.05
    
    # วนลูปเช็คพอร์ตสำคัญ
    for port in COMMON_PORTS:
        if check_port(ip, port, timeout=scan_timeout):
            active_ports.append(port)
            is_up = True
    
    # ถ้าเจอ Port เปิดค่อยไปหาชื่อเครื่อง (ลดภาระการ Resolve Name)
    if is_up:
        try: 
            # ลดเวลา lookup hostname
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
    
    # กำหนดค่าให้ OpenCV ใช้ FFMPEG และลดความหน่วง (Latency)
    # 5,000,000 คือ 5 วินาที สำหรับ timeout
    import os
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;udp|timeout;5000000"

    # เปิดกล้องโดยระบุ API Preference เป็น FFMPEG
    camera = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    
    # ตั้งค่า Buffer ให้เหลือน้อยที่สุดเพื่อให้ภาพเป็น Real-time
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    if not camera.isOpened():
        print(f"[ERROR] ไม่สามารถเชื่อมต่อกล้องได้: {rtsp_url}")
        return

    try:
        while True:
            success, frame = camera.read()
            if not success:
                # กรณีภาพกระตุก ให้ลองอ่านซ้ำ หรือ Reconnect
                time.sleep(0.1) 
                continue

            # ปรับขนาดภาพเล็กน้อยเพื่อประหยัด Bandwidth ของ Raspberry Pi
            # (ช่วยให้ดูผ่านมือถือได้ลื่นขึ้น)
            frame = cv2.resize(frame, (800, 450)) 

            # Encode เป็น JPG (คุณภาพ 70% กำลังดีสำหรับ Streaming)
            ret, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if not ret:
                continue
                
            frame_bytes = buffer.tobytes()
            
            # ส่งข้อมูลแบบ Multipart Stream
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
    user = request.args.get('user', 'admin') # ค่าเริ่มต้นเป็น admin
    pwd = request.args.get('pwd')
    path = request.args.get('path', 'onvif1') # จากรูปของคุณคือ onvif1
    port = request.args.get('port',554)

    # สร้าง URL ตามรูปแบบที่คุณใช้ใน VLC (ภาพ 1000011587.png)
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
    
    # ถ้า subnet ไม่มีจุดต่อท้าย ให้เติมจุด (ป้องกัน error)
    if not subnet.endswith('.'):
        subnet += '.'

    print(f"Scanning subnet: {subnet}1 - {subnet}254")
    
    # เพิ่ม Workers เป็น 200 เพื่อความรวดเร็วสูงสุดใน LAN
    with ThreadPoolExecutor(max_workers=100) as executor:
        ips = [f"{subnet}{i}" for i in range(1, 255)]
        # ใช้ map จะรอจนครบทุกตัว แต่ด้วย timeout 0.05s จะเสร็จในไม่กี่วินาที
        results = list(filter(None, executor.map(quick_scan_host, ips)))
    
    # เรียงลำดับผลลัพธ์ตามเลข IP (แปลงเป็น int เพื่อให้เรียงถูกต้อง 1, 2, 10 ไม่ใช่ 1, 10, 2)
    results.sort(key=lambda x: int(x['ip'].split('.')[-1]))
    
    return jsonify({'results': results})

@app.route('/deep_scan', methods=['POST'])
def deep_scan():
    """API: สแกน 65,535 Ports (Deep Scan)"""
    target_ip = request.json.get('ip')
    print(f"Deep scanning target: {target_ip}")
    
    open_ports = []
    max_port = 65535
    
    # ใช้ workers สูงๆ สำหรับ Deep Scan
    # และแบ่งช่วง Port (Chunking) ถ้าจำเป็น แต่ Python จัดการไหว
    with ThreadPoolExecutor(max_workers=200) as executor:
        # ใช้ timeout 0.1 สำหรับ deep scan เพื่อความชัวร์
        futures = {executor.submit(check_port, target_ip, p, 0.05): p for p in range(1, max_port + 1)}
        
        # ใช้ as_completed เพื่อไม่ต้องรอเรียงลำดับ (เร็วกว่ารอตามคิว)
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
    
    # สร้าง URL ตามรูปแบบ RTSP มาตรฐาน
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
        
        return jsonify({"status": "ok", "angle": state["angle"]})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


# ล้างค่า GPIO เมื่อปิดโปรแกรม
def cleanup():
    if servo :
        servo.angle = 0
        sleep(0.2)
        servo.value = None

if __name__ == '__main__':
    try:

        app.run(host='0.0.0.0', port=8000, debug=False, threaded=True)
    except KeyboardInterrupt:
        #cleanup()
        print("close")
    finally:
        #cleanup()

        print("close")
