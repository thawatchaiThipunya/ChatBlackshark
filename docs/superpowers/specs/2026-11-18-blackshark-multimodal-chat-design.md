# Black Shark multimodal chat and persistent history

**Status:** draft for review
**Date:** 2026-11-18

## Goal

Adapt the relevant input and product-history patterns from `ChatBotProductMS` to the smaller Black Shark chatbot. Treat Supabase as the durable source for chat sessions, messages, product history, attachments, and vision results. Do not persist chat content in `localStorage`.

## Reference patterns

- `ChatBotProductMS/ChatAdminWeb/src/backend/service/messageMediaParser.ts` normalizes Shopee `raw_payload` and fallback placeholders into typed messages. It handles text, image, image-with-text, video, product and variation cards, order cards, stickers, notifications, FAQ/live-agent events, and bundle messages, including known Shopee type aliases.
- `ChatBotProductMS/chatbot/shopeechat/chat_v2.py` resolves product-card `item_id` into an anchor, keeps a per-conversation product timeline, and reuses prior vision descriptions in follow-up turns.
- `ChatBotProductMS/chatbot/shopeechat/conversation_products.py` separates customer anchors from bot suggestions and resolves references such as “ตัวเดิม/อันนี้” from the conversation's active product.
- Reuse these behaviors as small Black Shark modules; do not import the other application's worker, admin, workflow, or order subsystems.

## Input contract and normalization

Keep plain-text `{question, session_id}` requests working. Extend the chat boundary to accept either normalized typed input or a Shopee `raw_payload` plus fallback text. A single parser converts inputs into a bounded internal message shape with `message_type`, text, media references, product/order references, and bundle children. Normalize `item_card`, `picture`, and `faq_liveagents`; handle `.0` item IDs; infer known types from legacy placeholders; preserve unknown types as safe, visible events rather than silently turning them into product questions.

Supported message types: `text`, `image`, `image_with_text`, `video`, `sticker`, `item`, `variation_card`, `order`, `bundle_message`/`bundle_deal`, `notification`, and `faq_liveagent`. Bundles are normalized into their contained events with bounded item counts. If a bundle payload only contains message IDs, the upstream adapter must provide resolved child payloads; this app will not query the reference system's admin/messages database. Order references are stored and shown as context only; order lookup is outside this feature.

FAQ/live-agent events default to the reference behavior: create a handoff/escalation record. A bot-reply mode may route the event's question through the normal FAQ/product grounding path. A handoff is reported as an escalation recorded in Supabase; it must not claim that a Shopee agent was notified unless an outbound channel is actually connected. Stickers and notifications are preserved and displayed; a sticker alone does not become product evidence.

The web test chat can submit typed card/media inputs for manual testing. The API contract also accepts the same normalized event from a future Shopee adapter. This repository currently has no Shopee inbound webhook or outbound sender, so platform delivery wiring is not implied by adding the parser.

## Product-card lookup and product history

On a customer `item` or `variation_card` event, use the exact `item_id` to perform a read-only lookup in the Shopee product collection (`MONGO_URI`, `MONGO_DB`, `MONGO_COLLECTION`, default collection `ShpProducts`). Restrict the lookup to the Black Shark shop where the source record provides shop identity. For that card turn, use the matched item's Mongo data to answer the question about the exact listing, then resolve its product name/model against the Black Shark catalog in Supabase and save the mapping in product history. Persist a bounded card snapshot/source marker for the first-turn audit if useful, but never treat that snapshot as product evidence on later turns.

Persist a per-session product timeline in Supabase with `item_id`, resolved Black Shark product/model ID and name, source (`customer_card`, `typed_model`, or `bot_suggestion`), timestamp, and anchor status. A new customer card or unambiguous model mention updates the active anchor; a bot suggestion is recorded separately. Pronouns and follow-ups resolve from that timeline. If model mapping is absent or ambiguous, ask a short clarification rather than guessing.

For every turn after the card turn, use the persisted product name/model mapping to retrieve Black Shark evidence from Supabase `bot_products` and `bot_faqs` first and answer from that data. Do not query Mongo or reuse the saved Mongo description as factual evidence on follow-up turns. If no reliable product mapping or Supabase evidence exists, say what is missing or ask a short clarification; do not silently fall back to Mongo. Product images returned by the Black Shark chat must come from the product data available to the answer path; the test UI renders typed product cards/media when present.

## Media and vision

Accept image and video attachments in the test chat, and media references from normalized platform input. Store original uploads in a private Supabase Storage bucket; store only object path, MIME type, byte size, message/session ownership, and processing status in the database. Generate short-lived upload/download URLs through server-controlled routes. External media URLs from `raw_payload` must be validated against allowed Shopee media hosts before fetching or storing them.

Run Gemini vision on new image/video attachments with the current question and a small same-session context. Persist the vision description, model, and processing status against the message/attachment. Include stored descriptions in subsequent history so the bot can understand references to prior images without re-running vision. Vision output provides conversational context; product specifications and compatibility still require Supabase KB/product evidence.

Use the Gemini Files API for larger/reusable video inputs and a bounded inline path for small images/videos, with explicit count/size/time limits. Supabase Storage supports private objects and time-limited signed upload URLs; Gemini supports video understanding, with inline input intended for files below 100 MB and the File API recommended for larger or reusable files. [Supabase signed uploads](https://supabase.com/docs/guides/storage/uploads/standard-uploads) · [Supabase private buckets](https://supabase.com/docs/guides/storage/buckets/fundamentals) · [Gemini video understanding](https://ai.google.dev/gemini-api/docs/video-understanding)

## Supabase persistence and access

Supabase is the source of truth for:

- guest-owned chat sessions and messages (customer input, bot answer, source references, and typed event metadata);
- product timeline/active anchor per session;
- attachment metadata and Gemini vision descriptions;
- escalation records for FAQ/live-agent handoff.

Store tables in the unexposed `bot_private` schema with RLS enabled. Keep file bytes in a private Storage bucket and reference them from attachment rows. The backend remains the only writer/reader of chat rows through server-controlled, token-gated RPCs; the `SUPABASE_SECRET_KEY` is server-only and used to issue scoped Storage upload/download URLs. Use an opaque, HTTP-only guest identity cookie to associate sessions with a browser; store only its token hash in Supabase, not chat content in the cookie. After refresh, the UI lists sessions and reloads messages from the API/database. React state may hold the active view or unsent draft in memory, but there will be no chat/session `localStorage` read or write.

The existing encrypted `bot_private.llm_keys` table and `/llm` key-management flow are reused. Do not create a second Gemini-key table.

The backend needs server-only Shopee product Mongo settings (`MONGO_URI`, `MONGO_DB`, `MONGO_COLLECTION`) and `SUPABASE_SECRET_KEY` for creating scoped Storage upload/download URLs. Keep these values out of browser bundles and chat records. The current Blackshark `.env` does not define the Mongo settings; the reference repo has its own Mongo configuration, which must be configured for this app's local and Vercel environments rather than exposed in source control.

## Data model sketch

- `bot_private.chat_guests`: guest id and cookie-token hash.
- `bot_private.chat_sessions`: id, guest id, title, active product, created/updated timestamps.
- `bot_private.chat_messages`: id, session id, role, normalized type, text, answer, sources, safe event metadata, timestamps.
- `bot_private.chat_products`: session id, item id, canonical product/model, source, anchor flag, mentioned timestamp.
- `bot_private.chat_attachments`: message id, private Storage object path, MIME/size, vision status/description/model.
- `bot_private.chat_settings`: single-store flags for FAQ/live-agent handling (`enabled`, `action=handoff|bot_reply`), defaulting to enabled handoff.

Add constraints/indexes for owner-scoped session listing and chronological message/product lookup. Never expose `bot_private` or media through public URLs.

## Failure handling

- If Mongo is unavailable for a new product card, retain the card event and ask the customer for the model name; do not fabricate product details.
- If Gemini vision fails, persist the media and a failed processing status, then answer any text portion from available KB evidence without claiming to have inspected the media.
- If Supabase persistence fails, do not display a successful persisted-history state; return a clear save error and avoid silently falling back to `localStorage`.
- Unsupported or malformed rich payloads are stored in a bounded safe form and get a neutral clarification/handoff, not a guessed product answer.
- A successful handoff response means the escalation row was created; it does not assert external staff notification.

## Out of scope

- Porting the ChatBotProductMS admin console, bot-worker, workflow engine, order database, or multi-platform behavior.
- Reading order contents from Mongo; only exact product-card lookups use Mongo in this feature.
- Claiming that an actual Shopee message, image, or live-agent handoff was sent. The current Black Shark project is a web chat/API and has no outbound Shopee sender.
- Retaining message or attachment content in browser local storage.

## Acceptance criteria

1. Existing plain-text chat still answers and keeps separate sessions.
2. Refreshing/reopening the same browser reloads sessions, messages, product timeline, media, and vision descriptions from Supabase; no chat data is read from or written to `localStorage`.
3. Each supported rich input type is normalized into a typed stored message; aliases, placeholder-only records, and bundle inputs are handled as specified.
4. A product card performs one exact, read-only Mongo lookup; that card turn can answer from the exact listing. The system resolves the listing's product/model name to Supabase and stores the mapping as the active anchor.
5. Follow-up turns search the mapped product in Supabase `bot_products`/`bot_faqs` first and use that evidence; they never query Mongo or fall back to the saved Mongo description.
6. Image/video originals are stored privately; Gemini vision descriptions are persisted and reused on follow-up turns.
7. FAQ/live-agent handoff creates a Supabase escalation record and the UI accurately describes the handoff state.
8. Gemini API keys continue to use the existing encrypted Supabase table.
