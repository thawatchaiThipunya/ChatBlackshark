# Python product chatbot

The backend answers product specification and basic troubleshooting questions from the two supplied Excel workbooks. It returns matching source text and the workbook cell reference. Payment, warranty/claim, tax-invoice, unknown-model, and no-match cases use the mocked handoff message; no staff notification is sent.

## Prepare source data

From the project root, run:

```powershell
python backend/scripts/prepare_knowledge_base.py
```

The script reads the two `.xlsx` files from the parent workspace directory and writes `backend/data/knowledge_base.json`. When source files move, pass `--catalog`, `--faq`, and optionally `--output`.

## Run offline

```powershell
python -m pip install -r backend/requirements.txt
python backend/scripts/prepare_knowledge_base.py
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000 --env-file .env
```

Copy `.env.example` to `.env`. Set `GEMINI_API_KEY` when available. Without it, PyThaiNLP and RapidFuzz still answer from the source workbooks. Gemini only ranks supplied excerpts and cannot create facts. The handoff is a mock display; no staff message or ticket is created.

To run the UI, use a second terminal:

```powershell
npm install
npm run dev
```

Open `http://localhost:3000`.

## API for the Next.js UI

### `GET /api/catalog`

Returns exact product and support-model labels for the UI selectors, plus source counts. The UI passes the selected label unchanged.

### `POST /api/chat`

Product specification request:

```json
{
  "product_name": "Black Shark T11",
  "question": "Game Mode"
}
```

Troubleshooting request:

```json
{
  "model_name": "Black Shark S1 Pro Smart Watch",
  "question": "ไมโครโฟนไม่มีเสียง"
}
```

Responses include `status`, `answer`, `intent`, `reason`, `matched_question` when applicable, and `sources` with the workbook, sheet, and cell reference. Possible statuses are `answered`, `need_model`, and `handoff_mock`.

For `handoff_mock`, the response is “ขอเช็กกับเจ้าหน้าที่ก่อน แล้วเดี๋ยวส่งกลับนะครับ”. `handoff_sent: false` confirms that no message is sent to staff.
