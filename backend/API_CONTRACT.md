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

## Legacy Shopee engine adapter

`POST /api/engine/token` accepts `Authorization: Bearer <CHATBLACKSHARK_TOKEN_ISSUER_SECRET>` and returns a signed access token with a 600-second lifetime. ChatBotProductMS should cache it, renew it before expiry, and refresh once after a `401`. `POST /api/engine/answer` accepts that access token as a Bearer token. During migration, it also continues to accept `LEGACY_ENGINE_TOKEN`.

The request identifies a message already mirrored to MongoDB. An optional `persona` object can override the answer style for this request; its supported fields are `bot_name`, `persona_text`, `friendliness` (0–100), `length` (`short|medium|long`), `detail_level` (0–100), and `voice` (`male|female`). Persona controls style only; product facts still come from this shop's scoped sources.

```json
{"conversation_id":"legacy-conversation-id","message_id":"inbound-message-id","shop_id":"1191420560","platform":"shopee","request_id":"optional-correlation-id","persona":{"bot_name":"Shop assistant","persona_text":"Friendly and concise","friendliness":70,"length":"medium","detail_level":50,"voice":"female"}}
```

The adapter reads `conversations_shp` and `messages_shp` by the exact platform, shop, conversation, and inbound message ID. It reads at most five prior question/answer pairs, including prior image descriptions from `shadow_replies`. It resolves product cards from `ShpProducts` and checks stock in each active `model_id`; at least one active unit with stock makes the listing available. For order questions it checks `ShpOrders` using the conversation customer ID and exact legacy shop name. Customer and order data are never read from another shop. Current media URLs are described by the configured vision provider in batches of up to five.

The response contains `answer`, `intent`, `reason`, `sources`, `product_cards`, `image_description`, `image_descriptions`, `handoff_required`, `handoff_reason`, `conversation_id`, and `message_id`. It also returns BotWorker-compatible aliases `source`, `products`, `image_desc`, and the caller's `request_id`. When `handoff_required` is true, ChatBotProductMS owns the actual handoff. This endpoint never sends a customer message, writes a shadow reply, opens a ticket, or changes BotWorker state. The caller should persist the answer and `image_description` using its existing BotWorker/shadow reply flow. It should use its existing buffer, debounce, workflow, review, and delivery controls. Retrying a request can regenerate an answer; the legacy caller owns idempotency by message ID.

`GET /api/tickets/shops`, `GET /api/tickets/conversations`, `GET /api/tickets/conversations/{conversation_id}/messages`, `GET /api/tickets/conversations/{conversation_id}/details`, and `POST /api/tickets/answer` power the authenticated `/tickets` read-only test UI. Message pages contain up to 100 rows and return `next_before_at` plus `next_before_id`; pass both as `before_at` and `before_id` to load older history without losing messages that share a timestamp. Testing an answer does not write to the legacy database.

### Shop isolation and configuration

Supabase `platforms`, `shopnames`, and `shop_files` determine supported `(platform, shop_id)` scopes. Product and FAQ rows are included only when their `source.file` is attached to that exact scope. A source filename attached to more than one shop is ambiguous and excluded from all scopes, so uploaded files need unique names per shop. A shop with no unambiguous attached KB files is unavailable to this engine. Commerce collections store only `shopname`, so the adapter also requires the conversation's legacy shop alias to map uniquely to one `shop_id` before reading product or order data. If the mapping is ambiguous, commerce lookup fails closed.

Set `CHATBLACKSHARK_TOKEN_ISSUER_SECRET` (shared with the ChatBotProductMS server) and `CHATBLACKSHARK_TOKEN_SIGNING_SECRET` (API only; use a separate random secret of at least 32 characters) on the Python API deployment. Keep `LEGACY_ENGINE_TOKEN` configured only while old callers are being migrated. Also set `LEGACY_ADMIN_MONGO_URI`, `LEGACY_ADMIN_MONGO_DB`, `LEGACY_COMMERCE_MONGO_URI`, and `LEGACY_COMMERCE_MONGO_DB`. Collection names default to `conversations_shp`, `messages_shp`, `shadow_replies`, `ShpProducts`, and `ShpOrders`; they can be overridden by the corresponding `LEGACY_*_COLLECTION` variables. The ChatBotProductMS server needs `CHATBLACKSHARK_ENGINE_URL` and the same `CHATBLACKSHARK_TOKEN_ISSUER_SECRET`. Never expose issuer, signing, legacy, or Mongo secrets to the browser.
