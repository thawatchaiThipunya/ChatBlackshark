# Black Shark Multimodal Chat Implementation Plan

> **For agentic workers:** Follow this plan task by task. Implementation stays in this session unless the user requests delegation.

**Goal:** Add rich Shopee-style inputs, Gemini image/video understanding, Supabase-persisted conversations and product history, and exact product-card anchoring to the Black Shark chatbot.

**Architecture:** Supabase is the durable source for guest sessions, messages, attachments, and product history. A small normalizer converts text and rich payloads into typed messages; Mongo is read-only and queried by exact `item_id` only when a product card arrives. The card turn can use that listing, while later turns resolve the saved product name/model to Supabase `bot_products` and `bot_faqs` only.

**Tech Stack:** Next.js 16, React 19, FastAPI/Pydantic, Supabase PostgREST/RPC and private Storage, PyMongo, Gemini `google-genai`.

**Spec:** `docs/superpowers/specs/2026-11-18-blackshark-multimodal-chat-design.md`

## Global Constraints

- Do not persist chat content in `localStorage`.
- On an `item` or `variation_card` event, look up the exact `item_id` in read-only Shopee Mongo; do not query Mongo on later turns.
- On turns after the card turn, retrieve product/FAQ evidence from Supabase `bot_products` and `bot_faqs`; do not reuse the Mongo description as facts.
- Keep original media in a private Supabase Storage bucket; keep attachment metadata and Gemini descriptions in Supabase DB.
- Keep `SUPABASE_SECRET_KEY`, Mongo settings, `LLM_KEY_STORE_TOKEN`, and Gemini keys on the server only.
- Reuse `bot_private.llm_keys`; do not add a duplicate Gemini-key table.
- Support normalized FAQ/live-agent handoff, but do not claim a Shopee agent or customer was messaged without an outbound integration.
- Do not query order data from Mongo in this feature.
- Do not add or run automated tests unless the user asks; use code review and a production build for implementation checks.

## Review Focus

1. Shopee sends numeric, string, or decimal-form `item_id`; normalize it without changing identity, query exact only, and reject a listing outside the Black Shark store.
2. A Mongo item name does not map exactly to Supabase; answer the card turn from the exact listing, but ask for clarification instead of inventing a persistent model mapping.
3. A follow-up uses pronouns after multiple products were mentioned; resolve the active customer anchor from the DB product timeline, not a stale card description.
4. A video upload is too large, has an unexpected MIME type, or Gemini Files API processing fails; keep the original privately, save the failed processing state, and do not claim vision succeeded.
5. Shopee sends placeholder-only, aliased, bundled, sticker, notification, or FAQ/live-agent payloads; normalize and store the event without interpreting it as unsupported product facts.

---

## File Structure and Interfaces

**Create:**

- `backend/app/chat_inputs.py` — normalize raw and typed incoming messages into a single bounded event model.
- `backend/app/shopee_products.py` — read-only exact lookup of one Black Shark Shopee product by `item_id`.
- `backend/app/vision.py` — image/video analysis through Gemini, returning description and processing metadata.
- `backend/migrations/003_persistent_multimodal_chat.sql` — private tables, indexes, RLS, and token-gated chat RPCs; this repo currently stores SQL migrations under `backend/migrations` and does not have the Supabase CLI installed.

**Modify:**

- `backend/app/main.py` — session, message, upload URL, and chat endpoints; accept legacy text requests plus normalized events.
- `backend/app/store.py` — DB/RPC methods for guests, sessions, messages, product timeline, attachment state, FAQ/live-agent settings, and signed Storage URLs.
- `backend/app/engine_v2.py` — call the input normalizer, use a Mongo card lookup only on the card turn, use DB product history as the follow-up anchor, and keep later product evidence Supabase-only.
- `backend/requirements.txt` — add `pymongo>=4.6,<5` from the reference project’s supported range.
- `.env.example`, `README.md`, and `backend/API_CONTRACT.md` — document server-only settings and the new API contract without writing secrets.
- `src/app/api/[...path]/route.ts` — proxy the new endpoints, pass the guest cookie to FastAPI, and preserve `Set-Cookie` responses.
- `src/lib/chat-utils.ts` — define typed chat request/reply and input event helpers.
- `src/app/page.tsx` and `src/app/globals.css` — load chats from the API, remove browser history persistence, add rich-input controls, and render saved media/events.

**Interfaces:**

```python
@dataclass(frozen=True)
class NormalizedChatInput:
    message_type: str
    text: str
    item_ids: tuple[str, ...]
    order_sn: str | None
    media: tuple[dict[str, str], ...]
    children: tuple["NormalizedChatInput", ...]
    metadata: dict[str, object]
```

The parser function is `normalize_chat_input(raw_payload: object | None, fallback_text: str = "") -> NormalizedChatInput`.

`SupabaseStore` methods are server-only wrappers around `public.bot_chat_action`; session RPCs receive the app token and guest token separately. The browser receives only opaque guest/session references and short-lived signed upload URLs.

## Tasks

### Task 1: Persist guests, sessions, messages, products, and media metadata

**Files:**

- Create: `backend/migrations/003_persistent_multimodal_chat.sql`
- Modify: `backend/app/store.py`
- Modify: `.env.example`

**Produces:** `chat_guests`, `chat_sessions`, `chat_messages`, `chat_products`, `chat_attachments`, and singleton `chat_settings` tables in `bot_private`; one server-only RPC `public.bot_chat_action(p_app_token text, p_guest_token text, p_action text, p_session_id uuid default null, p_payload jsonb default '{}'::jsonb)`; `SupabaseStore` wrappers with typed results.

- [ ] Create tables with foreign keys, bounded fields, `created_at`/`updated_at`, chronological/session indexes, a unique guest token hash, and owner-scoped session constraints. Store message payloads as normalized typed JSON, not unbounded source payloads.
- [ ] Enable RLS on every private table and revoke direct table access from `public`, `anon`, and `authenticated`.
- [ ] Implement the RPC with a fixed empty `search_path`, server app-token validation against `bot_private.admin_gate`, guest-token hash validation, and an allowlist of actions (`create_guest`, `list_sessions`, `create_session`, `load_session`, `save_message`, `save_product`, `save_attachment`, `update_vision`, `live_agent_settings`). Every session action verifies that the session belongs to the guest.
- [ ] Add matching `SupabaseStore` wrappers. Keep calls through the existing REST helper; do not add a frontend Supabase client for private data.
- [ ] Add `SUPABASE_SECRET_KEY`, `MONGO_URI`, `MONGO_DB`, `MONGO_COLLECTION`, and media-size settings as blank/documented server variables in `.env.example`.
- [ ] Create a private `chat-media` Storage bucket with allowed image/video MIME types and a configured byte limit. Use the Supabase connection for the bucket operation; do not expose its signing secret.
- [ ] Apply the SQL migration to the linked Supabase project and verify only the new objects exist, while leaving existing `llm_keys`, `escalations`, `bot_products`, and `bot_faqs` intact.

### Task 2: Add guest-scoped chat and signed-upload endpoints

**Files:**

- Modify: `backend/app/main.py`
- Modify: `backend/app/store.py`
- Modify: `src/app/api/[...path]/route.ts`

**Consumes:** `SupabaseStore` persistence methods from Task 1.

**Produces:** a `Secure`, `HttpOnly`, `SameSite=Lax` guest cookie; endpoints for listing/creating sessions, loading session messages, requesting a signed upload URL, and posting a chat turn.

- [ ] Add `POST /api/chat/sessions`: create a guest token and session on first use, set the guest cookie, and return the session id/title; with an existing valid cookie, create only the session.
- [ ] Add `GET /api/chat/sessions` and `GET /api/chat/sessions/{session_id}/messages`; both use the cookie token and return only that guest’s rows.
- [ ] Add `POST /api/chat/media/upload-url` that validates session ownership, safe filename, MIME type, and size before returning a signed upload URL and generated object path.
- [ ] Change `POST /api/chat` so the backend loads recent history from Supabase by session id, persists the normalized user event, processes it, and persists the assistant reply/source/analysis before returning it. Return a clear save failure; do not fall back to browser persistence.
- [ ] Update the Next.js proxy allowlist for the new paths; forward the incoming cookie as an internal guest token, and relay `Set-Cookie` from FastAPI to the browser. Do not relay `SUPABASE_SECRET_KEY` or `LLM_KEY_STORE_TOKEN`.
- [ ] Keep the existing `/api/chat` plain-text body accepted for clients that include a valid guest/session context.

### Task 3: Normalize Shopee and web-chat input types

**Files:**

- Create: `backend/app/chat_inputs.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/engine_v2.py`
- Modify: `backend/API_CONTRACT.md`

**Consumes:** the request schema and DB-backed session history from Task 2.

**Produces:** `normalize_chat_input(raw_payload, fallback_text) -> NormalizedChatInput` and an input contract that accepts old text requests, typed events, or reference-style Shopee `raw_payload`.

- [ ] Normalize types `text`, `image`, `image_with_text`, `video`, `sticker`, `item`, `variation_card`, `order`, `bundle_message`, `notification`, and `faq_liveagent`; aliases include `item_card`, `picture`, `faq_liveagents`, and `bundle_deal`.
- [ ] Normalize `.0` numeric ids, known text placeholders, nested content shapes, image/video URL fields, sticker id/package fallback, order number, and bundle `source_content.item_id`.
- [ ] Cap a turn at five media attachments and ten bundle children. Preserve bundle child objects supplied by the caller; keep unresolved message-id strings as safe metadata and do not query another app’s message database.
- [ ] Bound raw metadata and ignore unknown fields. Represent unknown message types as visible `unknown` events rather than generating product answers from them.
- [ ] For a malformed or unknown rich event with no usable text, return a neutral clarification or the configured handoff; never pass it to product search as if it were a normal product question.
- [ ] Store sticker, notification, order-card, and FAQ/live-agent events as typed chat messages. Do not look up order data. Default FAQ/live-agent action to `handoff`; when configured `bot_reply`, route its text through the existing grounded KB/product answer path. Only report a handoff row recorded, not that Shopee notified staff.
- [ ] Update the API contract examples for all accepted event shapes and the exact `item_id` card request.

### Task 4: Implement exact Mongo card lookup and durable product timeline

**Files:**

- Create: `backend/app/shopee_products.py`
- Modify: `backend/app/engine_v2.py`
- Modify: `backend/app/store.py`
- Modify: `backend/requirements.txt`

**Consumes:** typed `item`/`variation_card` messages and Supabase product-history methods.

**Produces:** `lookup_shopee_item(item_id: str) -> ShopeeProduct | None`, where `ShopeeProduct` contains only canonical `item_id`, title/name, description, store identity, image references, and source metadata needed for the current card turn.

- [ ] Use a shared lazy `MongoClient` configured by `MONGO_URI`, `MONGO_DB` (default `dbWallet`), and `MONGO_COLLECTION` (default `ShpProducts`). Open it read-only in code: only `find_one`, never insert/update/delete.
- [ ] Query exact `item_id`, tolerating the reference DB’s integer, float, and string representations. Reject an explicit non-Black-Shark store/brand record.
- [ ] On a new card event, use that exact Mongo record for the card turn. Resolve the listing name/model to a single Supabase `bot_products`/`bot_faqs` model using normalized exact model/name matches; save the mapping, source=`customer_card`, and active anchor to Supabase. If the mapping is ambiguous, keep the item identity but do not bind an internal model.
- [ ] If Mongo is unavailable or no exact item exists, preserve the typed card event and ask for the model name; do not invent listing details.
- [ ] Record bot-recommended product names separately with source=`bot_suggestion`; do not let a suggestion override the last customer card anchor.
- [ ] On follow-up turns, resolve pronouns from persisted product history and retrieve factual product/FAQ evidence only from Supabase. If no exact internal model mapping/evidence exists, ask a short clarification and never use the Mongo description as fallback.
- [ ] Record `item_id`, mapped product/model, source, anchor, timestamp, and a bounded first-turn card snapshot/source marker so each answer can be audited without treating the snapshot as later-turn evidence.

### Task 5: Store media and run Gemini vision once per new attachment

**Files:**

- Create: `backend/app/vision.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/store.py`
- Modify: `backend/app/engine_v2.py`
- Modify: `.env.example`

**Consumes:** private Storage object paths and normalized media messages.

**Produces:** `describe_media(object_path, mime_type, question, history) -> VisionResult(description, model, status)`.

- [ ] Upload browser files directly to the private bucket using the signed URL from Task 2; the file bytes do not pass through a Vercel Function. The initial limit is five attachments per turn and 100 MiB per object, configurable by `CHAT_MEDIA_MAX_BYTES`.
- [ ] Accept image MIME types JPEG, PNG, WebP, and GIF; accept video MIME types MP4, WebM, and QuickTime. Reject unsupported MIME, mismatched extension, and oversize uploads before issuing a signed URL.
- [ ] Fetch each private object server-side and call Gemini vision with the current question plus bounded same-session context. Use inline bytes for supported small media and Gemini Files API for videos. Use existing `SupabaseStore.next_key()` rotation; never send chat text/history to a provider on upload URL creation.
- [ ] Persist attachment object path, MIME, byte size, status, Gemini model, and description in `chat_attachments`; persist failures too. Reuse stored descriptions on follow-up requests and never call vision on the same attachment twice unless processing failed and the user retries.
- [ ] When vision fails, continue answering only any accompanying text that has Supabase evidence, and state that the media could not be read; do not imply it was inspected.
- [ ] Use the description as context only. Product specifications, compatibility, and troubleshooting facts still require Supabase product/FAQ evidence.
- [ ] Update `backend/requirements.txt` only if the installed `google-genai` version lacks the required Files API; keep the existing supported major version range and document the minimum API used.

### Task 6: Move the chat UI fully to Supabase and add rich input controls

**Files:**

- Modify: `src/app/page.tsx`
- Modify: `src/lib/chat-utils.ts`
- Modify: `src/app/globals.css`
- Modify: `src/app/api/[...path]/route.ts`

**Consumes:** DB-backed session/message API, normalized event contract, and signed media upload endpoint.

**Produces:** a chat UI whose persistent sessions/messages come from the API; no chat/session `localStorage` or `sessionStorage` reads/writes.

- [ ] On page load, get sessions from the API and load messages for the selected session; after refresh, use the session id in the URL if valid, otherwise open/create the newest session from Supabase.
- [ ] Create a session through the API before rendering it as saved. Keep unsent draft only in React memory. Add pagination for older messages rather than loading unbounded history.
- [ ] Remove `STORAGE_KEY`, `ACTIVE_SESSION_KEY`, `readSessions`, localStorage writes, and sessionStorage writes. Do not migrate old local sessions automatically; they remain browser-only and are not treated as DB history.
- [ ] Extend the composer with text, image/video upload, item/variation card `item_id`, order card reference, sticker media, bundle child events, notification, and FAQ/live-agent event modes for manual testing.
- [ ] Extend assistant messages with structured product references from Supabase product data; render product names and available image URLs as product cards. Render saved images, videos, stickers, notifications, bot answers, sources, and escalation state from typed API responses. Keep media links private via short-lived signed download URLs.
- [ ] Replace the “เก็บไว้ใน browser นี้” notice with a short DB-history notice and clarify when a live-agent event is recorded versus actually delivered.

### Task 7: Configure local/Vercel runtime and document operation

**Files:**

- Modify: `.env.example`
- Modify: `README.md`
- Modify: `backend/README.md`
- Modify: `backend/API_CONTRACT.md`

**Consumes:** all API and storage behavior from Tasks 1–6.

**Produces:** local and Vercel configuration instructions with secrets kept outside source control.

- [ ] Document `MONGO_URI`, `MONGO_DB`, `MONGO_COLLECTION`, `SUPABASE_SECRET_KEY`, existing Supabase URL/publishable key/token/encryption key, `CHAT_MEDIA_MAX_BYTES`, and the current Gemini model variables. Do not copy values into tracked files.
- [ ] Populate local ignored `.env` from the already configured Black Shark Shopee product connection where authorized; check that `MONGO_DB=dbWallet` and collection is `ShpProducts` without printing credentials.
- [ ] Check existing Vercel project env names first. Set only missing server-side settings for the API project; never put secret keys in `NEXT_PUBLIC_*` variables.
- [ ] Update `backend/API_CONTRACT.md` with session, upload, rich input, history, source-priority, and handoff responses. Update local run instructions for the new backend variables.
- [ ] Run the production build (`npm run build`) and start the app locally; use a small manual smoke flow for reload persistence, one item-card turn followed by an internal-Supabase-only follow-up, one image description, one video description, and FAQ/live-agent recording. Do not call the manual flow an automated test.
- [ ] Deploy the production code to the existing Vercel project after Supabase schema/storage and required environment settings are present; inspect deployment state and one live smoke request before reporting it ready.

## Delivery Order

Complete Tasks 1–2 first so every later input can be stored durably. Then implement normalization (Task 3), product anchor/source priority (Task 4), media/vision (Task 5), UI integration/removal of browser persistence (Task 6), and runtime/deployment docs (Task 7). The key acceptance point is that a card turn may read its exact Mongo listing, while the next turn can only retrieve internal Supabase facts through the persisted product-name/model mapping.
