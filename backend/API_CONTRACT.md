# Black Shark product chat API

Base URL: `http://127.0.0.1:8001`. Product knowledge is read from Supabase. The API keeps no shared conversation state.

## `GET /api/health`

Returns `{ "status": "ok" }`.

## `GET /api/catalog`

Returns product and support-model labels plus source counts:

```json
{
  "products": [{ "name": "Black Shark T11" }],
  "support_models": ["Black Shark S1 Pro Smart Watch"],
  "counts": { "products": 44, "support_models": 32, "faq_entries": 462 }
}
```

## `POST /api/chat`

The web client sends a session ID and only that chat's recent history. The backend matches one exact catalog model from the current question, or from the latest user turns in that session. When the match is ambiguous or missing, it asks for clarification without calling Gemini. An explicit `product_name` or `model_name` is still accepted for API clients that already know the catalog label.

```json
{
  "session_id": "unique-chat-id",
  "question": "รุ่นนี้มี Game Mode ไหม",
  "history": [{"role": "user", "content": "Black Shark T11 มีอะไรเด่นบ้าง"}]
}
```

The exact `product_name` used for retrieval and Gemini is returned with the source evidence. Status is `answered`, `need_model`, `key_required`, or `handoff_mock`.

Payment/refund, warranty/claim, tax invoice, unknown products, or missing source information return:

```json
{
  "status": "handoff_mock",
  "answer": "ขอเช็กกับเจ้าหน้าที่ก่อน แล้วเดี๋ยวส่งกลับนะครับ",
  "handoff_mock": true,
  "handoff_sent": false,
  "sources": []
}
```

`key_required` means add a Gemini key through `/llm`. `handoff_mock` only displays a message; no ticket or staff notification is created or sent. Gemini receives evidence for one matched product only and is rejected when it adds unsupported facts.
