# Black Shark Product Chat

เว็บแชทหน้าเดียวสำหรับข้อมูลสินค้าและวิธีแก้ปัญหาเบื้องต้น Python คัดหลักฐานจาก Supabase ซึ่งนำเข้าจาก Excel สองไฟล์ที่ให้มา แล้ว Gemini เรียบเรียงคำตอบจากหลักฐานนั้น

## เริ่มใช้งานในเครื่อง

ต้องมี Python 3.11+ และ Node.js 22+ จากโฟลเดอร์โปรเจกต์

1. ติดตั้ง Python dependencies (ทำครั้งเดียว):

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r backend/requirements.txt
   ```

2. เปิด backend ใน terminal แรก:

   ```bash
   .venv/bin/uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8001 --env-file .env
   ```

3. เปิด Next.js ใน terminal ที่สอง:

   ```bash
   npm install
   npm run dev -- --port 3001
   ```

4. เปิด `http://127.0.0.1:3001` โดย Next.js จะส่งคำขอ `/api/*` ไปยัง `http://127.0.0.1:8001` ผ่าน `PYTHON_API_URL`

ระบบโหลดสินค้าและคู่มือจาก Supabase หน้าแชทไม่บังคับเลือกสินค้า แต่จับรุ่นจากคำถามและประวัติของแชทนั้น หากยังระบุไม่ได้จะถามกลับก่อนค้นข้อมูล ผู้ใช้เข้าสู่ระบบผ่าน System81 SSO ก่อนใช้งานทุกหน้า และเพิ่ม Gemini API key ได้ที่ `/llm` หลังผ่าน SSO

หน้าแชทเก็บหลาย session แยกกันใน browser และส่งเฉพาะประวัติของ session ที่กำลังถามไปยัง API

## Deploy บน Vercel

ใช้ GitHub repo เดียวสร้าง Vercel สองโปรเจกต์ เพื่อให้ Python และ Next.js build แยกกัน:

1. **Python API:** Import repo โดยตั้ง Root Directory เป็น `backend` และ Framework Preset เป็น FastAPI ใช้ค่าจาก `backend/requirements.txt` โดยอัตโนมัติ หลัง deploy ตรวจ `https://<backend-domain>/api/health` ให้ได้ `{"status":"ok"}`
2. **Next.js:** Import repo เดิมอีกครั้ง โดยตั้ง Root Directory เป็นราก repo และ Framework Preset เป็น Next.js ตั้ง Environment Variable `PYTHON_API_URL=https://<backend-domain>` โดยไม่เติม `/api` ท้าย URL จากนั้น deploy แล้วเปิดหน้าเว็บเพื่อทดลองถามคำถาม
3. ตั้งค่า `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `LLM_KEY_STORE_TOKEN`, `LLM_KEY_ENCRYPTION_KEY`, `SUPABASE_SECRET_KEY`, `PYTHAINLP_DATA=/tmp/pythainlp-data` และตัวแปร SSO เฉพาะ Python API แล้ว deploy อีกครั้ง

หลัง SSO สำเร็จ เปิด `/llm` เพื่อเพิ่ม Gemini API key ได้หลายคีย์ บอทใช้ลำดับ round robin จาก Supabase และลองคีย์ถัดไปเมื่อเรียกคีย์หนึ่งล้มเหลว หน้าเว็บเห็นเพียงชื่อและท้ายคีย์ 4 ตัว คีย์เต็มเข้ารหัสด้วย Fernet ก่อนเก็บในตาราง `bot_private.llm_keys`

หน้าเว็บเรียก `/api/*` บนโดเมนตัวเอง แล้ว Next.js ส่งต่อไปยัง Python API ฝั่ง server จึงไม่ต้องเปิด CORS ให้โดเมนสาธารณะ และไม่เปิดเผย Gemini key ใน browser

## ขอบเขตการตอบ

- Supabase Postgres: 44 รายการสินค้า, 32 รุ่นที่มีคู่มือ, 462 หัวข้อแก้ปัญหา
- Backend จับชื่อรุ่นจาก catalog แบบตรงตัว ใช้รุ่นจากประวัติของ session เดียวกันได้ แล้วส่งเฉพาะหลักฐานของรุ่นนั้นให้ Gemini หากมีหลายรุ่นหรือหลักฐานไม่พอจะไม่เดาคำตอบ
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
# Persistent chat setup

Apply `backend/migrations/003_persistent_multimodal_chat.sql` to the linked Supabase project before starting this version. It adds private guest, session, message, product-history, attachment, and settings tables plus the private `chat-media` bucket. The API uses the existing `LLM_KEY_STORE_TOKEN` to call its gated chat RPC. Configure `MONGO_URI`, `MONGO_DB`, `MONGO_COLLECTION`, and `SUPABASE_SECRET_KEY` on the Python backend. The secret key signs private media uploads and fetches media for Gemini; never expose it through `NEXT_PUBLIC_*`. The web chat creates an HTTP-only guest cookie and saves each turn in Supabase; a browser refresh reloads the saved session.

## SSO console setup

Apply `backend/migrations/004_admin_console.sql` to Supabase after migration 003. Set `APP_BASE_URL` to the public Next.js origin on the Python API. Optional SSO variables are `SELLCENTER_OAUTH_BASE_URL` (defaults to `https://data.digital.in.th`), `SSO_APP_NAME` (defaults to `Black Shark Chat`), and `SSO_AUTO_PROVISION_DOMAIN` (defaults to `@itsr.co.th`). The SSO callback provisions a local 8-hour session and stores only its SHA-256 hash. Admin actions and preferences are recorded in `bot_private.admin_logs`; structured API request logs go to the Python service logs.
