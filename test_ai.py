import cv2
import time
import math
import face_recognition
import numpy as np
from ultralytics import YOLO

# 1. โหลดโมเดล YOLO และตั้งค่าการคำนวณความเร็ว
yolo_model = YOLO('yolov8n.pt')
track_history = {}
PIXEL_TO_METER = 0.03 # 1 พิกเซล = 0.03 เมตร (ปรับแก้ตามความกว้างถนนจริง)

# 2. เปิดกล้อง (0 คือกล้องหลักของบอร์ด)
cap = cv2.VideoCapture(0)

while cap.isOpened():
    ret, frame = cap.read()
    if not ret: break
    
    # ย่อขนาดเฟรมลดภาระ CPU
    frame = cv2.resize(frame, (640, 360))

    # 3. รัน YOLO ในโหมด Track (ตรวจจับ คน=0, รถยนต์=2, มอเตอร์ไซค์=3)
    results = yolo_model.track(frame, classes=[0, 2, 3], conf=0.5, persist=True, verbose=False)
    
    for r in results:
        if r.boxes.id is None: continue
        
        for box, track_id, cls in zip(r.boxes.xyxy, r.boxes.id.int().tolist(), r.boxes.cls):
            x1, y1, x2, y2 = map(int, box)
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            
            # 4. คำนวณความเร็ว (เก็บประวัติพิกัดย้อนหลัง)
            if track_id not in track_history: track_history[track_id] = []
            track_history[track_id].append((cx, cy, time.time()))
            if len(track_history[track_id]) > 30: track_history[track_id].pop(0)

            speed_kmh = 0
            if len(track_history[track_id]) >= 5:
                p1, t1 = track_history[track_id][0][:2], track_history[track_id][0][2]
                p2, t2 = track_history[track_id][-1][:2], track_history[track_id][-1][2]
                dt = t2 - t1
                if dt > 0:
                    speed_mps = (math.hypot(p2[0]-p1[0], p2[1]-p1[1]) * PIXEL_TO_METER) / dt
                    speed_kmh = speed_mps * 3.6

            # 5. แยกประเภท และตรวจจับใบหน้าคน
            label = f"Vehicle ID:{track_id} {int(speed_kmh)}km/h"
            color = (255, 0, 0) # สีน้ำเงิน (รถ)
            
            if int(cls) == 0:
                color = (0, 165, 255) # สีส้ม (คน)
                label = f"Person ID:{track_id} {int(speed_kmh)}km/h"
                
                # ครอปภาพเฉพาะคนไปรันหาหน้า (ประหยัด CPU)
                try:
                    crop = frame[max(0, y1-10):y2+10, max(0, x1-10):x2+10]
                    rgb_crop = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                    face_locs = face_recognition.face_locations(rgb_crop)
                    
                    if face_locs:
                        # ถ้ามีฐานข้อมูลใบหน้า ก็เอามา compare() ตรงนี้ได้เลย
                        label = f"Face Detected! {int(speed_kmh)}km/h"
                        color = (0, 255, 0) # เปลี่ยนเป็นสีเขียวเมื่อเจอหน้า
                except Exception:
                    pass
            
            # 6. วาดกรอบและแสดงผล
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame, label, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

    cv2.imshow("AI Speed & Face Tracking", frame)
    
    # กด 'q' เพื่อออก
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
