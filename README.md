# Black Shark Product Chat

เว็บแชทหน้าเดียวสำหรับข้อมูลสินค้าและวิธีแก้ปัญหาเบื้องต้น Python คัดหลักฐานจาก Supabase ซึ่งนำเข้าจาก Excel สองไฟล์ที่ให้มา แล้ว Gemini เรียบเรียงคำตอบจากหลักฐานนั้น

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

4. เปิด `http://localhost:3000` โดย Next.js จะส่งคำขอ `/api/*` ไปยัง Python API ผ่าน `PYTHON_API_URL`

กรอกค่าจาก `.env.example` ก่อนรัน ระบบต้องเชื่อม Supabase และมี Gemini key อย่างน้อยหนึ่งคีย์จึงจะตอบข้อมูลสินค้าได้ หากยังไม่มีคีย์ ระบบจะแสดง mock handoff การทดสอบ offline ใช้ fake Gemini ใน regression tests

## Deploy บน Vercel

ใช้ GitHub repo เดียวสร้าง Vercel สองโปรเจกต์ เพื่อให้ Python และ Next.js build แยกกัน:

1. **Python API:** Import repo โดยตั้ง Root Directory เป็น `backend` และ Framework Preset เป็น FastAPI ใช้ค่าจาก `backend/requirements.txt` โดยอัตโนมัติ หลัง deploy ตรวจ `https://<backend-domain>/api/health` ให้ได้ `{"status":"ok"}`
2. **Next.js:** Import repo เดิมอีกครั้ง โดยตั้ง Root Directory เป็นราก repo และ Framework Preset เป็น Next.js ตั้ง Environment Variable `PYTHON_API_URL=https://<backend-domain>` โดยไม่เติม `/api` ท้าย URL จากนั้น deploy แล้วเปิดหน้าเว็บเพื่อทดลองถามคำถาม
3. ตั้งค่า `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `LLM_KEY_STORE_TOKEN`, `LLM_KEY_ENCRYPTION_KEY`, `ADMIN_PASSWORD`, `PYTHAINLP_DATA=/tmp/pythainlp-data` เฉพาะ Python API แล้ว deploy อีกครั้ง

เปิด `/llm` บนหน้าเว็บ กรอกรหัสผู้ดูแลจาก `ADMIN_PASSWORD` แล้วเพิ่ม Gemini API key ได้หลายคีย์ บอทใช้ลำดับ round robin จาก Supabase และลองคีย์ถัดไปเมื่อเรียกคีย์หนึ่งล้มเหลว หน้าเว็บเห็นเพียงชื่อและท้ายคีย์ 4 ตัว คีย์เต็มเข้ารหัสด้วย Fernet ก่อนเก็บในตาราง `bot_private.llm_keys`

หน้าเว็บเรียก `/api/*` บนโดเมนตัวเอง แล้ว Next.js ส่งต่อไปยัง Python API ฝั่ง server จึงไม่ต้องเปิด CORS ให้โดเมนสาธารณะ และไม่เปิดเผย Gemini key ใน browser

## ขอบเขตการตอบ

- Supabase Postgres: 44 รายการสินค้า, 32 รุ่นที่มีคู่มือ, 462 หัวข้อแก้ปัญหา
- Python ใช้ RapidFuzz เลือกข้อความต้นทางตามรุ่นและคำถาม แล้วส่งเฉพาะข้อความนั้นให้ Gemini เรียบเรียง หากไม่มีคีย์หรือคำตอบตรวจไม่ผ่านจะ mock handoff
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

จากนั้นนำข้อมูลที่เปลี่ยนเข้า Supabase ด้วย `backend/scripts/sql_import_batch.py` และเรียก SQL ที่ได้ตามชุดผ่าน Supabase SQL Editor หรือเครื่องมือต่อฐานข้อมูล ตารางใช้ `source_index` เพื่ออัปเดตข้อมูลเดิมได้ ระหว่างรัน Python จะ cache ข้อมูลฐานข้อมูล 60 วินาที
