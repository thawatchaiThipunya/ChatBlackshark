"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, ArrowDown, Bot, ChevronDown, Clock3, ExternalLink, LoaderCircle,
  MessageSquareText, Package, RefreshCw, Search, Send, ShoppingBag, Store, UserRound } from "lucide-react";
import "./tickets.css";

type Shop = { platform: string; platform_name: string; shop_id: string; shop_name: string; files: string[] };
type Conversation = { conversation_id: string; customer_id?: string; shop_id: string; shop_name: string;
  platform: string; to_name?: string; last_message_text?: string; last_message_timestamp?: string;
  unread_count?: number; status?: string };
type ProductCard = { item_id?: string; name?: string; image_url?: string; product_name?: string;
  availability?: string; description?: string; models?: Array<{ model_id?: string; model_name?: string;
    model_status?: string; available?: number | null; stock_known?: boolean }> };
type Message = { id: string; message_id?: string; role: "user" | "assistant"; label?: string; content: string;
  created_at?: string; message_type?: string; media?: Array<{ url: string; kind: string }>;
  vision_descriptions?: string[]; image_description?: string; product_cards?: ProductCard[];
  handoff_required?: boolean; source?: string };
type Order = { order_sn: string; order_status: string; logistics_status: string; shipping_carrier: string;
  tracking_numbers: string[]; items: Array<{ name: string; model_name: string; quantity: number }>;
  create_time?: string };
type Detail = { conversation: Conversation; shop: Shop; orders: Order[]; orders_unavailable?: boolean };
type AnswerResult = { status: string; bot_status?: string; intent?: string; reason?: string; answer: string;
  handoff_required?: boolean; handoff_reason?: string; image_description?: string;
  product_cards?: ProductCard[]; sources?: Array<{ file?: string; sheet?: string; cell?: string }>;
  intent_analysis?: { topic?: string; needs?: string[]; product_names?: string[]; category?: string; unresolved?: boolean } };

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, cache: "no-store", headers: {
    ...(init?.body ? { "Content-Type": "application/json" } : {}), ...(init?.headers || {}),
  } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || body.error || `HTTP ${response.status}`);
  return body as T;
}

function timeLabel(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("th-TH", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(date);
}

function InlineText({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return <>{parts.map((part, index) => part.startsWith("**") && part.endsWith("**")
    ? <strong key={index}>{part.slice(2, -2)}</strong> : <span key={index}>{part}</span>)}</>;
}

function MessageText({ text }: { text: string }) {
  const lines = text.replaceAll("\\|", "|").split("\n");
  const blocks: Array<{ kind: "text"; text: string } | { kind: "table"; rows: string[][] }> = [];
  for (let index = 0; index < lines.length;) {
    if (lines[index].trim().startsWith("|") && lines[index].trim().endsWith("|")) {
      const rows: string[][] = [];
      while (index < lines.length && lines[index].trim().startsWith("|") && lines[index].trim().endsWith("|")) {
        const row = lines[index].trim().slice(1, -1).split("|").map((cell) => cell.trim());
        if (!row.every((cell) => /^:?-{3,}:?$/.test(cell))) rows.push(row);
        index += 1;
      }
      if (rows.length > 1) blocks.push({ kind: "table", rows });
      else if (rows.length) blocks.push({ kind: "text", text: rows[0].join(" · ") });
      continue;
    }
    const paragraph: string[] = [];
    while (index < lines.length && lines[index].trim() && !(lines[index].trim().startsWith("|") && lines[index].trim().endsWith("|"))) {
      paragraph.push(lines[index]); index += 1;
    }
    if (paragraph.length) blocks.push({ kind: "text", text: paragraph.join("\n") });
    else index += 1;
  }
  return <div className="ticket-message-copy">{blocks.map((block, index) => block.kind === "table"
    ? <div className="ticket-table-wrap" key={index}><table><thead><tr>{block.rows[0].map((cell, cellIndex) => <th key={cellIndex}><InlineText text={cell} /></th>)}</tr></thead>
      <tbody>{block.rows.slice(1).map((row, rowIndex) => <tr key={rowIndex}>{block.rows[0].map((_, cellIndex) => <td key={cellIndex}><InlineText text={row[cellIndex] || "—"} /></td>)}</tr>)}</tbody></table></div>
    : <p key={index}><InlineText text={block.text} /></p>)}</div>;
}

function ProductTile({ item }: { item: ProductCard }) {
  return <article className="ticket-product-tile">
    {item.image_url ? <img src={item.image_url} alt="" loading="lazy" /> : <span className="ticket-product-placeholder"><Package size={18} /></span>}
    <div><strong>{item.name || "สินค้า"}</strong>{item.product_name && <small>KB: {item.product_name}</small>}
      {item.item_id && <small>item_id · {item.item_id}</small>}
      {item.models?.length ? <div className="ticket-variants">{item.models.slice(0, 5).map((model, index) => <span key={model.model_id || index}>
        {model.model_name || "รุ่นย่อย"}{model.stock_known ? ((model.available || 0) > 0 ? " · มีสินค้า" : " · หมด") : " · ไม่ทราบสต็อก"}
      </span>)}</div> : null}
    </div>
  </article>;
}

export default function TicketsPage() {
  const [shops, setShops] = useState<Shop[]>([]);
  const [shopKey, setShopKey] = useState("");
  const selectedShop = useMemo(() => shops.find((shop) => `${shop.platform}:${shop.shop_id}` === shopKey), [shops, shopKey]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<"customer" | "orders" | "products">("customer");
  const [loading, setLoading] = useState(true);
  const [messageLoading, setMessageLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [olderCursor, setOlderCursor] = useState<{ at: string; id: string } | null>(null);
  const [busyMessage, setBusyMessage] = useState("");
  const [answers, setAnswers] = useState<Record<string, AnswerResult>>({});
  const [error, setError] = useState("");
  const [listError, setListError] = useState("");
  const [mobilePane, setMobilePane] = useState<"list" | "chat">("list");
  const scrollRef = useRef<HTMLDivElement>(null);
  const requestSeq = useRef(0);

  const loadShops = useCallback(async () => {
    setLoading(true); setListError("");
    try {
      const result = await api<{ shops: Shop[] }>("/api/tickets/shops");
      setShops(result.shops || []);
      if (!shopKey && result.shops?.length) setShopKey(`${result.shops[0].platform}:${result.shops[0].shop_id}`);
    } catch (caught) { setListError(caught instanceof Error ? caught.message : "โหลดรายชื่อร้านไม่สำเร็จ"); }
    finally { setLoading(false); }
  }, [shopKey]);

  const loadConversations = useCallback(async (quiet = false) => {
    if (!selectedShop) return;
    if (!quiet) { setLoading(true); setListError(""); }
    const params = new URLSearchParams({ platform: selectedShop.platform, shop_id: selectedShop.shop_id, q: query, page: "1", limit: "80" });
    try {
      const result = await api<{ conversations: Conversation[] }>(`/api/tickets/conversations?${params}`);
      setConversations(result.conversations || []);
      if (selected && !result.conversations.some((row) => row.conversation_id === selected.conversation_id)) {
        setSelected(null); setMessages([]); setDetail(null);
      }
    } catch (caught) { setListError(caught instanceof Error ? caught.message : "โหลดแชทไม่สำเร็จ"); }
    finally { if (!quiet) setLoading(false); }
  }, [selectedShop, query, selected]);

  const openConversation = useCallback(async (conversation: Conversation) => {
    const sequence = ++requestSeq.current;
    setSelected(conversation); setMobilePane("chat"); setMessageLoading(true); setError("");
    setMessages([]); setDetail(null); setAnswers({});
    const params = new URLSearchParams({ platform: conversation.platform, shop_id: conversation.shop_id });
    try {
      const [messageOutcome, detailOutcome] = await Promise.allSettled([
        api<{ messages: Message[]; has_more: boolean; next_before_at?: string; next_before_id?: string }>(`/api/tickets/conversations/${encodeURIComponent(conversation.conversation_id)}/messages?${params}`),
        api<Detail>(`/api/tickets/conversations/${encodeURIComponent(conversation.conversation_id)}/details?${params}`),
      ]);
      if (sequence !== requestSeq.current) return;
      if (messageOutcome.status === "rejected") throw messageOutcome.reason;
      setMessages(messageOutcome.value.messages || []); setHasMore(messageOutcome.value.has_more);
      setOlderCursor(messageOutcome.value.next_before_at && messageOutcome.value.next_before_id ?
        { at: messageOutcome.value.next_before_at, id: messageOutcome.value.next_before_id } : null);
      setDetail(detailOutcome.status === "fulfilled" ? detailOutcome.value : null);
      requestAnimationFrame(() => { if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight; });
    } catch (caught) { if (sequence === requestSeq.current) setError(caught instanceof Error ? caught.message : "โหลดประวัติแชทไม่สำเร็จ"); }
    finally { if (sequence === requestSeq.current) setMessageLoading(false); }
  }, []);

  useEffect(() => { void loadShops(); }, [loadShops]);
  useEffect(() => { if (selectedShop) void loadConversations(); }, [selectedShop, loadConversations]);
  useEffect(() => {
    if (!selectedShop) return;
    const timer = window.setInterval(() => { void loadConversations(true); }, 20000);
    return () => window.clearInterval(timer);
  }, [selectedShop, loadConversations]);

  const loadOlder = useCallback(async () => {
    if (!selected || !selectedShop || !hasMore || loadingMore || !messages.length) return;
    if (!olderCursor) return;
    const node = scrollRef.current;
    const oldHeight = node?.scrollHeight || 0; const oldTop = node?.scrollTop || 0;
    setLoadingMore(true);
    const params = new URLSearchParams({ platform: selected.platform, shop_id: selected.shop_id,
      before_at: olderCursor.at, before_id: olderCursor.id, limit: "100" });
    try {
      const result = await api<{ messages: Message[]; has_more: boolean; next_before_at?: string; next_before_id?: string }>(`/api/tickets/conversations/${encodeURIComponent(selected.conversation_id)}/messages?${params}`);
      setMessages((current) => [...result.messages, ...current]); setHasMore(result.has_more);
      setOlderCursor(result.next_before_at && result.next_before_id ?
        { at: result.next_before_at, id: result.next_before_id } : null);
      requestAnimationFrame(() => { if (node) node.scrollTop = oldTop + (node.scrollHeight - oldHeight); });
    } catch (caught) { setError(caught instanceof Error ? caught.message : "โหลดประวัติเพิ่มไม่สำเร็จ"); }
    finally { setLoadingMore(false); }
  }, [selected, selectedShop, hasMore, loadingMore, messages, olderCursor]);

  async function answerMessage(message: Message) {
    if (!selected) return;
    setBusyMessage(message.id); setError("");
    try {
      const result = await api<AnswerResult>("/api/tickets/answer", { method: "POST", body: JSON.stringify({
        conversation_id: selected.conversation_id, message_id: message.message_id || message.id,
        shop_id: selected.shop_id, platform: selected.platform,
      }) });
      setAnswers((current) => ({ ...current, [message.id]: result }));
    } catch (caught) { setError(caught instanceof Error ? caught.message : "ระบบตอบคำถามไม่สำเร็จ"); }
    finally { setBusyMessage(""); }
  }

  const allProducts = useMemo(() => {
    const found: ProductCard[] = [];
    messages.forEach((message) => found.push(...(message.product_cards || [])));
    Object.values(answers).forEach((answer) => found.push(...(answer.product_cards || [])));
    const unique = new Map<string, ProductCard>();
    for (const item of found) unique.set(item.item_id || item.name || String(unique.size), item);
    return [...unique.values()];
  }, [messages, answers]);

  return <main className="ticket-page">
    <header className="ticket-page-head">
      <div className="ticket-page-title"><span><MessageSquareText size={19} /></span><div><h1>Tickets จากระบบร้าน</h1><p>อ่านแชทจาก Mongo แล้วให้ Black Shark engine ทดลองตอบ โดยไม่เขียนทับข้อความในระบบร้าน</p></div></div>
      <div className="ticket-head-actions">
        <label className="ticket-shop-select"><Store size={16} /><select aria-label="เลือกร้านและแพลตฟอร์ม" value={shopKey} onChange={(event) => { setShopKey(event.target.value); setSelected(null); setMessages([]); }}>
          {shops.map((shop) => <option key={`${shop.platform}:${shop.shop_id}`} value={`${shop.platform}:${shop.shop_id}`}>{shop.shop_name} · {shop.platform_name}</option>)}
        </select><ChevronDown size={14} /></label>
        <button type="button" className="ticket-refresh" onClick={() => { void loadShops(); void loadConversations(); }} aria-label="รีเฟรช"><RefreshCw size={16} /></button>
      </div>
    </header>
    {error && <div className="ticket-error"><AlertCircle size={16} /><span>{error}</span><button onClick={() => setError("")}>ปิด</button></div>}
    {listError && <div className="ticket-error"><AlertCircle size={16} /><span>{listError}</span><button onClick={() => void loadConversations()}>ลองใหม่</button></div>}
    <div className={`ticket-workspace${mobilePane === "chat" ? " mobile-chat" : " mobile-list"}`}>
      <aside className="ticket-inbox">
        <div className="ticket-inbox-heading"><div><strong>กล่องข้อความ</strong><small>{conversations.length} ห้องในร้านที่เลือก</small></div><button type="button" onClick={() => void loadConversations()} title="รีเฟรชแชท"><RefreshCw size={15} /></button></div>
        <label className="ticket-search"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void loadConversations(); }} placeholder="ค้นหาชื่อหรือข้อความ" /><button type="button" onClick={() => void loadConversations()} aria-label="ค้นหา"><Send size={14} /></button></label>
        <div className="ticket-conversation-list">
          {loading && !conversations.length ? <div className="ticket-loading"><LoaderCircle size={20} /> กำลังโหลดห้องแชท</div> : null}
          {!loading && !conversations.length && !listError ? <div className="ticket-empty"><ShoppingBag size={21} /><strong>ยังไม่มีห้องแชท</strong><span>ระบบจะแสดงเฉพาะร้านที่มีไฟล์ความรู้ผูกไว้</span></div> : null}
          {conversations.map((conversation) => <button type="button" key={conversation.conversation_id}
            className={`ticket-conversation${selected?.conversation_id === conversation.conversation_id ? " is-active" : ""}`}
            onClick={() => void openConversation(conversation)}>
            <span className="ticket-customer-avatar"><UserRound size={17} /></span>
            <span className="ticket-conversation-copy"><strong>{conversation.to_name || `ลูกค้า ${conversation.customer_id || ""}`}</strong>
              <small>{conversation.last_message_text || "มีข้อความใหม่"}</small></span>
            <span className="ticket-conversation-meta"><time>{timeLabel(conversation.last_message_timestamp)}</time>
              {!!conversation.unread_count && <b>{conversation.unread_count}</b>}</span>
          </button>)}
        </div>
      </aside>

      <section className="ticket-chat-panel" aria-label="บทสนทนา">
        {selected ? <>
          <header className="ticket-chat-head">
            <button type="button" className="ticket-back" onClick={() => setMobilePane("list")}>← กล่องข้อความ</button>
            <span className="ticket-customer-avatar"><UserRound size={17} /></span>
            <div><strong>{selected.to_name || `ลูกค้า ${selected.customer_id || ""}`}</strong><small>{selected.platform} · {selected.shop_name}</small></div>
            <span className="ticket-readonly-badge"><span /> อ่านอย่างเดียว</span>
          </header>
          <div className="ticket-message-list" ref={scrollRef} onScroll={(event) => { if (event.currentTarget.scrollTop < 60) void loadOlder(); }}>
            {hasMore && <button className="ticket-load-older" type="button" disabled={loadingMore} onClick={() => void loadOlder()}>{loadingMore ? <LoaderCircle size={14} /> : <ArrowDown size={14} />} โหลดข้อความเก่าเพิ่ม</button>}
            {messageLoading ? <div className="ticket-loading"><LoaderCircle size={20} /> กำลังอ่าน history</div> : null}
            {!messageLoading && !messages.length && <div className="ticket-empty"><MessageSquareText size={21} /><strong>ไม่มีข้อความในห้องนี้</strong></div>}
            {messages.map((message) => <div className={`ticket-message-row ${message.role}`} key={message.id}>
              {message.role === "assistant" && <span className="ticket-bot-avatar"><Bot size={15} /></span>}
              <article className={`ticket-bubble ${message.role}`}>
                <div className="ticket-bubble-meta"><strong>{message.label || (message.role === "user" ? "ลูกค้า" : "ร้านค้า")}</strong><time>{timeLabel(message.created_at)}</time></div>
                {message.content && <MessageText text={message.content} />}
                {!!message.media?.length && <div className="ticket-media-grid">{message.media.map((media, index) => media.kind === "video"
                  ? <video key={index} controls preload="metadata" src={media.url} />
                  : <a key={index} href={media.url} target="_blank" rel="noreferrer"><img src={media.url} alt="ภาพจากลูกค้า" loading="lazy" /><ExternalLink size={13} /></a>)}</div>}
                {!!message.vision_descriptions?.length && <details className="ticket-vision-note"><summary>คำอธิบายภาพที่ระบบเดิมเก็บไว้</summary>{message.vision_descriptions.map((description, index) => <p key={index}>{description}</p>)}</details>}
                {!!message.product_cards?.length && <div className="ticket-product-cards">{message.product_cards.map((item, index) => <ProductTile key={item.item_id || index} item={item} />)}</div>}
                {message.role === "user" && <button type="button" className="ticket-answer-button" onClick={() => void answerMessage(message)} disabled={!!busyMessage}>
                  {busyMessage === message.id ? <><LoaderCircle size={14} /> กำลังคิดคำตอบ</> : <><Bot size={14} /> ทดลองให้บอทตอบ</>}
                </button>}
                {answers[message.id] && <div className={`ticket-generated-answer${answers[message.id].handoff_required ? " needs-handoff" : ""}`}>
                  <div className="ticket-generated-head"><Bot size={14} /><strong>{answers[message.id].handoff_required ? "ส่งต่อให้ระบบร้านพิจารณา" : "คำตอบจาก Black Shark engine"}</strong>
                    <span>{answers[message.id].intent || "ตอบคำถาม"}</span></div>
                  <MessageText text={answers[message.id].answer} />
                  {!!answers[message.id].product_cards?.length && <div className="ticket-product-cards">{answers[message.id].product_cards?.map((item, index) => <ProductTile key={item.item_id || index} item={item} />)}</div>}
                  {answers[message.id].image_description && <details className="ticket-vision-note"><summary>คำอธิบายภาพที่ส่งให้โมเดล</summary><p>{answers[message.id].image_description}</p></details>}
                  <details className="ticket-vision-note"><summary>วิเคราะห์คำถามและหลักฐาน</summary>
                    <p>Intent: {answers[message.id].intent || "ไม่ระบุ"}{answers[message.id].reason ? ` · เหตุผล: ${answers[message.id].reason}` : ""}</p>
                    {!!answers[message.id].intent_analysis?.needs?.length && <p>ความต้องการ: {answers[message.id].intent_analysis?.needs?.join(" · ")}</p>}
                    {!!answers[message.id].intent_analysis?.product_names?.length && <p>รุ่นที่อ้างถึง: {answers[message.id].intent_analysis?.product_names?.join(" · ")}</p>}
                  </details>
                  {!!answers[message.id].sources?.length && <small className="ticket-source-line">แหล่งข้อมูล: {answers[message.id].sources?.map((source) => source.file).filter(Boolean).join(" · ")}</small>}
                </div>}
              </article>
              {message.role === "user" && <span className="ticket-customer-avatar"><UserRound size={15} /></span>}
            </div>)}
          </div>
          <footer className="ticket-chat-footer"><span><Clock3 size={14} /> ทดลองตอบโดยไม่บันทึกและไม่ส่งกลับไปหาลูกค้า</span><code>{selected.conversation_id}</code></footer>
        </> : <div className="ticket-select-prompt"><span><MessageSquareText size={25} /></span><h2>เลือกห้องแชทเพื่อดูบทสนทนา</h2><p>อ่านประวัติจาก Mongo ตาม conversation_id และดูข้อมูลเฉพาะร้านที่เลือก</p></div>}
      </section>

      <aside className="ticket-info-panel">
        <div className="ticket-info-tabs" role="tablist" aria-label="ข้อมูลแชท">
          <button type="button" className={tab === "customer" ? "active" : ""} onClick={() => setTab("customer")}>ข้อมูล</button>
          <button type="button" className={tab === "orders" ? "active" : ""} onClick={() => setTab("orders")}>ออเดอร์ {detail?.orders.length ? <i>{detail.orders.length}</i> : null}</button>
          <button type="button" className={tab === "products" ? "active" : ""} onClick={() => setTab("products")}>สินค้า</button>
        </div>
        {!selected ? <div className="ticket-info-empty"><Store size={20} /><span>เลือกห้องแชทเพื่อดูรายละเอียด</span></div> : <div className="ticket-info-content">
          {tab === "customer" && <>
            <div className="ticket-info-section"><span className="ticket-info-icon"><UserRound size={17} /></span><div><small>ลูกค้า</small><strong>{selected.to_name || "ไม่ระบุชื่อ"}</strong><small>Customer ID · {selected.customer_id || "ไม่ระบุ"}</small></div></div>
            <div className="ticket-info-section"><span className="ticket-info-icon"><Store size={17} /></span><div><small>ร้านและช่องทาง</small><strong>{detail?.shop.shop_name || selected.shop_name}</strong><small>{detail?.shop.platform_name || selected.platform} · Shop ID {selected.shop_id}</small></div></div>
            <div className="ticket-data-note"><strong>ขอบเขตข้อมูลที่ใช้</strong><p>KB จะถูกเลือกจากไฟล์ที่ผูกกับคู่นี้เท่านั้น: {selected.platform} + {selected.shop_id}</p><small>{selectedShop?.files.join(" · ")}</small></div>
            <div className="ticket-data-note neutral"><strong>การทำงานของหน้านี้</strong><p>โหลด messages และ shadow replies จาก Mongo อ่าน order/stock จากฐานร้าน และเรียกโมเดลตอบโดยไม่เขียนข้อมูลกลับ</p></div>
          </>}
          {tab === "orders" && <>
            <div className="ticket-info-title"><ShoppingBag size={16} /><strong>ประวัติคำสั่งซื้อ</strong></div>
            {detail?.orders_unavailable ? <div className="ticket-info-empty">ระบบออเดอร์ยังไม่พร้อม โปรดลองใหม่อีกครั้ง</div>
            : !detail ? <div className="ticket-info-empty">ยังโหลดข้อมูลออเดอร์ไม่ได้</div>
            : detail.orders.length ? detail.orders.map((order) => <article className="ticket-order-card" key={order.order_sn}>
              <header><strong>{order.order_sn}</strong><span>{order.order_status}</span></header><p>{order.logistics_status}{order.shipping_carrier ? ` · ${order.shipping_carrier}` : ""}</p>
              {order.tracking_numbers.map((number) => <small key={number}>Tracking · {number}</small>)}
              {order.items.map((item, index) => <div className="ticket-order-item" key={`${item.name}:${index}`}><Package size={14} /><span>{item.name} {item.model_name && `· ${item.model_name}`}</span><small>×{item.quantity}</small></div>)}
            </article>) : <div className="ticket-info-empty">ไม่พบคำสั่งซื้อของลูกค้ารายนี้ในร้านนี้</div>}
          </>}
          {tab === "products" && <>
            <div className="ticket-info-title"><Package size={16} /><strong>สินค้าในการสนทนา</strong></div>
            {allProducts.length ? allProducts.map((item, index) => <ProductTile key={item.item_id || index} item={item} />)
              : <div className="ticket-info-empty">ยังไม่มีการ์ดสินค้าในประวัติห้องนี้</div>}
          </>}
        </div>}
      </aside>
    </div>
  </main>;
}
