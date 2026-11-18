# Black Shark Product Chat

เว็บแชทถามตอบข้อมูลสินค้าและวิธีแก้ปัญหาเบื้องต้น โดยใช้ข้อมูลจากไฟล์ Excel สองไฟล์ที่ให้มาเท่านั้น

## เริ่มใช้งานในเครื่อง

ต้องมี Python 3.11+ และ Node.js 22+

1. เปิด PowerShell ที่โฟลเดอร์โปรเจกต์ แล้วติดตั้ง Python dependencies:

   ```powershell
   python -m pip install -r backend/requirements.txt
   Copy-Item .env.example .env
   ```

2. เปิด backend ใน terminal แรก:

   ```powershell
   python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000 --env-file .env
   ```

3. เปิด Next.js ใน terminal ที่สอง:

   ```powershell
   npm install
   npm run dev
   ```

4. เปิด `http://localhost:3000`

ไม่ต้องใส่ Gemini key เพื่อทดลองแบบ offline; บอทใช้ PyThaiNLP และ RapidFuzz ค้นจากข้อมูลในเครื่องได้ ส่วน Gemini เป็นตัวเลือกที่ช่วยจัดอันดับข้อความต้นทางเมื่อมี key และไม่เขียนข้อเท็จจริงใหม่

## ขอบเขตการตอบ

- 44 รายการสินค้า, 32 รุ่นที่มีคู่มือ, 462 หัวข้อแก้ปัญหา
- คำตอบแสดงชื่อไฟล์และตำแหน่งต้นทางเมื่อจับคู่ข้อมูลได้
- คำถามชำระเงิน/คืนเงิน, ประกัน/เคลม, ใบกำกับภาษี, รุ่นหรือข้อมูลที่ไม่มี และผลค้นหาไม่ชัดเจน ใช้ข้อความ mock handoff: “ขอเช็กกับเจ้าหน้าที่ก่อน แล้วเดี๋ยวส่งกลับนะครับ”
- Mock handoff แสดงบนหน้าจอเท่านั้น ยังไม่มีการส่งข้อความหรือตั๋วถึงเจ้าหน้าที่

## Regression tests

รันทั้ง UI helper และ Python bot tests จากโฟลเดอร์โปรเจกต์:

```powershell
npm test
```

## ปรับปรุงข้อมูล

เมื่อแก้ workbook ให้รัน:

```powershell
python backend/scripts/prepare_knowledge_base.py
```

แล้วเริ่ม backend ใหม่
