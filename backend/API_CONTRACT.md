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

The adapter reads `conversations_shp` and `messages_shp` by the exact platform, shop, conversation, and inbound message ID. It reads at most five prior question/answer pairs, including prior image descriptions from `shadow_replies`. For product answers, the only `ShpProducts` lookup is for IDs on a customer card, variation, or bundle; Mongo supplies the listing ID and title only, which are used to resolve the exact product record in this shop's Supabase KB. The model receives the matched KB product and FAQ evidence, not Mongo descriptions, color options, unit stock, or listing claims. If any card ID cannot be matched to a KB product, the response uses the existing `handoff_required` response shape.

Order status uses `ShpOrders` scoped by the current conversation's `customer_id` and uniquely mapped legacy shop alias. An explicit order card or order number is matched exactly. Without an explicit order number, the adapter answers only when the customer's order list or the requested product context identifies one order; otherwise it asks which product or order number the customer means. Troubleshooting may read that same customer's purchased item names to resolve a KB product when the chat has no clear model; it sends only the resolved product identity into KB/FAQ retrieval, never order descriptions as product evidence. Stock lookup is not used by this answer engine. Current media URLs are described by the configured vision provider in batches of up to five.

The legacy input adapter unwraps Shopee's `data.content.message_type/content` envelope as well as the root-level form. It reads text and image-with-text content, image/video URLs, item IDs, variation `product_id`, order numbers, bundle `source_content.item_id` and referenced submessage IDs, and notification text. Bundle submessages are loaded only from the same conversation, shop, and platform. For images it prefers a full-size URL over the thumbnail; video payloads may include both the video and its thumbnail. A sticker URL is used when present. Otherwise, when package/sticker IDs are available, the adapter checks Shopee's region manifest and package metadata for the file extension, caches successful metadata responses in process, then builds the CDN URL; it falls back to PNG if metadata cannot be read. The region comes from the message or `SHOPEE_STICKER_REGION` (default `th`). The media downloader allows only the configured Shopee/Supabase hosts, including the Shopee sticker CDN. `faq_liveagent` returns the existing immediate `handoff_required` response; notification and rating-card events without customer text receive a non-LLM acknowledgement.

The adapter recognizes these stored `raw_payload` event types:

| `message_type` | Data used by this engine |
| --- | --- |
| `text` | Text from the content envelope or `messages_shp.text`. |
| `image`, `image_with_text` | Image URL plus any text in the same payload; the vision description is passed with the question. |
| `video` | Video URL and optional thumbnail; media is described before the answer step. |
| `item`, `variation_card` | `item_id` or `product_id` resolves listing identity, then the exact shop's KB supplies answer facts. |
| `order` | `order_sn` identifies a customer order for status questions. |
| `sticker` | Uses its direct image URL, or resolves package/sticker IDs through Shopee's CDN metadata and sends the image to vision. |
| `bundle_message`, `bundle_deal` | Uses the bundle item ID and/or referenced child message IDs; referenced rows are constrained to the same conversation, platform, and shop. Each resolved item must map to this shop's KB. |
| `faq_liveagent` | Returns the immediate handoff flag; ChatBotProductMS decides and executes the handoff. |
| `notification`, `rating_card` | Preserves platform-event meaning for display and returns a short acknowledgement without calling the answer model when no customer text exists. |
| Unsupported type | Returns a safe clarification/handoff state; it is not treated as a product question. |

The adapter accepts up to ten media references per message and describes them in batches of five. It resolves up to twenty referenced bundle messages. It does not pass Mongo product descriptions, stock, or variation claims as answer evidence; card IDs are identity hints only, and product/FAQ facts must come from the selected shop's Supabase KB.

The response contains `answer`, `intent`, `reason`, `sources`, `product_cards`, `image_description`, `image_descriptions`, `handoff_required`, `handoff_reason`, `handoff_explanation`, `conversation_id`, and `message_id`. `handoff_reason` is a stable reason code and `handoff_explanation` is a short human-readable explanation; both are present whenever `handoff_required` is true. The engine asks one clarification question when product/order context or a required detail is initially ambiguous; if the follow-up still does not provide enough information, it returns the handoff flag. Legal threats, consumer-protection complaints, and formal marketplace/platform disputes are handed off immediately. If the selected shop KB/FAQ cannot substantiate a requested fact, the answer/review stage returns `insufficient_shop_evidence` for staff follow-up. It also returns BotWorker-compatible aliases `source`, `products`, `image_desc`, and the caller's `request_id`. When `handoff_required` is true, ChatBotProductMS owns the actual handoff. This endpoint never sends a customer message, writes a shadow reply, opens a ticket, or changes BotWorker state. The caller should persist the answer and `image_description` using its existing BotWorker/shadow reply flow. It should use its existing buffer, debounce, workflow, review, and delivery controls. Retrying a request can regenerate an answer; the legacy caller owns idempotency by message ID.

The request's exact `(platform, shop_id)` resolves the active shop and its KB before intent processing. The engine passes that server-resolved shop ID and name as trusted context; intent only interprets which merchant the customer's words refer to and cannot change the active shop. A brand or product name alone is not treated as a different store. If a customer explicitly asks for another store, the engine identifies the active shop in a polite, no-handoff reply and does not search or recommend products from the active shop as if they belonged to the requested merchant. For direct compatibility or suitability questions, the answer starts with a supported yes/no/not-confirmed verdict before giving the relevant KB evidence; specifications alone are not treated as an answer to the customer's decision question.

For `comparison` intent, the API returns a renderable Markdown table with the compared KB product names as column headings. If the answer or review step returns prose/bullets instead, a format-only pass runs against the same draft and KB evidence; it may add one provider call for that comparison. Clients should preserve Markdown so their table renderer can display it.

`GET /api/tickets/shops`, `GET /api/tickets/conversations`, `GET /api/tickets/conversations/{conversation_id}/messages`, `GET /api/tickets/conversations/{conversation_id}/details`, and `POST /api/tickets/answer` power the authenticated `/tickets` read-only test UI. Message pages contain up to 100 rows and return `next_before_at` plus `next_before_id`; pass both as `before_at` and `before_id` to load older history without losing messages that share a timestamp. Testing an answer does not write to the legacy database.

### Shop isolation and configuration

Supabase `platforms`, `shopnames`, and `shop_files` determine supported `(platform, shop_id)` scopes. Product and FAQ rows are included only when their `source.file` is attached to that exact scope. A source filename attached to more than one shop is ambiguous and excluded from all scopes, so uploaded files need unique names per shop. A shop with no unambiguous attached KB files is unavailable to this engine. Commerce collections store only `shopname`, so the adapter also requires the conversation's legacy shop alias to map uniquely to one `shop_id` before reading card identity or order data. If the mapping is ambiguous, commerce lookup fails closed.

Set `CHATBLACKSHARK_TOKEN_ISSUER_SECRET` (shared with the ChatBotProductMS server) and `CHATBLACKSHARK_TOKEN_SIGNING_SECRET` (API only; use a separate random secret of at least 32 characters) on the Python API deployment. Keep `LEGACY_ENGINE_TOKEN` configured only while old callers are being migrated. Also set `LEGACY_ADMIN_MONGO_URI`, `LEGACY_ADMIN_MONGO_DB`, `LEGACY_COMMERCE_MONGO_URI`, and `LEGACY_COMMERCE_MONGO_DB`. Collection names default to `conversations_shp`, `messages_shp`, `shadow_replies`, `ShpProducts`, and `ShpOrders`; they can be overridden by the corresponding `LEGACY_*_COLLECTION` variables. The ChatBotProductMS server needs `CHATBLACKSHARK_ENGINE_URL` and the same `CHATBLACKSHARK_TOKEN_ISSUER_SECRET`. Never expose issuer, signing, legacy, or Mongo secrets to the browser.
