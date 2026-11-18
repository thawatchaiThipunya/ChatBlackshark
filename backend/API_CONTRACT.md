# Black Shark product chat API

Base URL: `http://localhost:8000`. The API is read-only and serves one product-support chat using only the two supplied workbooks.

## `GET /api/health`

Returns `{ "status": "ok" }`.

## `GET /api/catalog`

Returns exact product and support-model labels for the chat selector, plus source counts:

```json
{
  "products": [{ "name": "Black Shark T11" }],
  "support_models": ["Black Shark S1 Pro Smart Watch"],
  "counts": { "products": 44, "support_models": 32, "faq_entries": 462 }
}
```

## `POST /api/chat`

Send a product specification question with `product_name`, or a troubleshooting question with `model_name`. Pass the exact label returned by `/api/catalog`.

```json
{ "product_name": "Black Shark T11", "question": "Game Mode" }
```

```json
{ "model_name": "Black Shark S1 Pro Smart Watch", "question": "ไมโครโฟนไม่มีเสียง" }
```

The response includes `status`, `answer`, `intent`, `reason`, `matched_question` when applicable, and `sources` with workbook, sheet, and cell references. Status is `answered`, `need_model`, or `handoff_mock`.

Payment/refund, warranty/claim, tax invoice, missing/unknown source information, and weak or ambiguous matches return:

```json
{
  "status": "handoff_mock",
  "answer": "ขอเช็กกับเจ้าหน้าที่ก่อน แล้วเดี๋ยวส่งกลับนะครับ",
  "handoff_mock": true,
  "handoff_sent": false,
  "sources": []
}
```

This only displays a mock handoff message. No ticket or staff notification is created or sent. Gemini, when configured, can only rank supplied source passages; the answer remains a source excerpt.
