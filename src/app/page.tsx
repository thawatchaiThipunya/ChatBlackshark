"use client";

import { Fragment, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import {
  ArrowUp,
  Bot,
  Check,
  CircleHelp,
  FileText,
  Headphones,
  Info,
  LoaderCircle,
  MessageCircle,
  Paperclip,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  UserRound,
  X,
} from "lucide-react";
import { buildSessionChatRequest, getCatalogStatus, mergeLoadedSessions, presentChatReply,
  shouldApplyInitialSession, type ChatReply, type ChatSource } from "@/lib/chat-utils";
import { parseChatAnswer } from "@/lib/chat-answer-format";

type Catalog = {
  products: Array<{ name: string; category?: string | null }>;
  support_models: string[];
  counts: { products: number; support_models: number; faq_entries: number };
};

type MessageKind = "welcome" | "answer" | "handoff" | "need_model" | "setup_required" | "error";
type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  kind: MessageKind;
  text: string;
  sources?: ChatSource[];
  productName?: string;
  productNames?: string[];
  handoffSent?: boolean;
  caseId?: number;
  imageUrl?: string;
  cardTitle?: string;
  productCards?: Array<{ itemId: string; title: string; imageUrl?: string; productName?: string }>;
  attachments?: string[];
  createdAt?: string;
};
type ChatSession = { id: string; title: string; updatedAt: number; messages: ChatMessage[] };

const GREETING: ChatMessage = {
  id: "welcome",
  role: "assistant",
  kind: "welcome",
  text: "สวัสดีครับ พิมพ์คำถามได้เลย ไม่ต้องเลือกสินค้าก่อน ผมจะจับรุ่นจากข้อความและบริบทในแชทนี้ครับ",
};

type StoredSession = { id: string; title: string; updated_at: string };
type StoredMessage = { id: string; role: "user" | "assistant"; content: string; created_at: string; payload?: Record<string, unknown> };

function readProductCards(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value.flatMap((candidate) => {
    if (!candidate || typeof candidate !== "object") return [];
    const card = candidate as Record<string, unknown>;
    if (typeof card.name !== "string") return [];
    return [{ itemId: typeof card.item_id === "string" ? card.item_id : "",
      title: card.name, imageUrl: typeof card.image_url === "string" ? card.image_url : undefined,
      ...(typeof card.product_name === "string" ? { productName: card.product_name } : {}) }];
  });
}

function renderLinkedText(text: string) {
  return text.split(/(https?:\/\/[^\s<]+)/g).map((part, index) => {
    if (!part.startsWith("http://") && !part.startsWith("https://")) return part;
    const url = part.replace(/[.,!?;:)\]]+$/, "");
    return <Fragment key={`${index}-${url}`}><a href={url} target="_blank" rel="noreferrer">{url}</a>{part.slice(url.length)}</Fragment>;
  });
}

function fromStoredSession(row: StoredSession): ChatSession {
  return { id: row.id, title: row.title, updatedAt: Date.parse(row.updated_at), messages: [GREETING] };
}

function fromStoredMessage(row: StoredMessage): ChatMessage {
  const payload = row.payload || {};
  const analysis = payload.intent_analysis && typeof payload.intent_analysis === "object"
    ? payload.intent_analysis as Record<string, unknown> : {};
  const productNames = Array.isArray(analysis.product_names)
    ? analysis.product_names.filter((name): name is string => typeof name === "string") : [];
  return {
    id: row.id, role: row.role, text: row.content,
    createdAt: row.created_at,
    kind: row.role === "user" ? "answer" : payload.status === "escalated" ? "handoff" :
      payload.status === "need_model" ? "need_model" : "answer",
    productName: typeof payload.product_name === "string" ? payload.product_name : undefined,
    productNames,
    sources: Array.isArray(payload.sources) ? payload.sources as ChatSource[] : [],
    handoffSent: payload.handoff_sent === true,
    caseId: typeof payload.case_id === "number" ? payload.case_id : undefined,
    imageUrl: row.role === "user" && typeof payload.image_url === "string" ? payload.image_url : undefined,
    cardTitle: row.role === "user" && typeof payload.card_title === "string" ? payload.card_title : undefined,
    productCards: row.role === "user" ? readProductCards(payload.product_cards) : [],
    attachments: Array.isArray(payload.attachment_ids) ? payload.attachment_ids.filter((id): id is string => typeof id === "string") : undefined,
  };
}

function MediaAttachment({ sessionId, attachmentId }: { sessionId: string; attachmentId: string }) {
  const [media, setMedia] = useState<{ url: string; mime_type: string } | null>(null);
  useEffect(() => {
    let active = true;
    void fetch(`/api/chat/sessions/${sessionId}/media/${attachmentId}`, { cache: "no-store" })
      .then((response) => response.ok ? response.json() as Promise<{ url: string; mime_type: string }> : null)
      .then((result) => { if (active && result) setMedia(result); })
      .catch(() => {});
    return () => { active = false; };
  }, [sessionId, attachmentId]);
  if (!media) return <span className="media-loading">กำลังโหลดสื่อ…</span>;
  return media.mime_type.startsWith("video/")
    ? <video className="message-media" controls preload="metadata" src={media.url} />
    : <img className="message-media" src={media.url} alt="ภาพที่ส่งในแชท" />;
}

function titleFromQuestion(question: string) {
  const clean = question.trim().replace(/\s+/g, " ");
  return clean.length > 34 ? `${clean.slice(0, 34)}…` : clean || "แชทใหม่";
}

export default function HomePage() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState(false);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [sessionsReady, setSessionsReady] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [itemIds, setItemIds] = useState<Record<string, string>>({});
  const [mediaFiles, setMediaFiles] = useState<Record<string, File[]>>({});
  const [sendingSessions, setSendingSessions] = useState<Set<string>>(new Set());
  const [composerErrors, setComposerErrors] = useState<Record<string, string>>({});
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [knowledgeOpen, setKnowledgeOpen] = useState(false);
  const [sessionSearch, setSessionSearch] = useState("");
  const listRef = useRef<HTMLDivElement>(null);
  const loadingOlderRef = useRef(false);
  const preserveScrollRef = useRef(false);
  const [hasOlder, setHasOlder] = useState<Record<string, boolean>>({});
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const navigationVersionRef = useRef(0);

  async function loadCatalog() {
    setCatalogLoading(true);
    setCatalogError(false);
    try {
      const response = await fetch("/api/catalog", { cache: "no-store" });
      if (!response.ok) throw new Error("catalog_unavailable");
      setCatalog((await response.json()) as Catalog);
    } catch {
      setCatalogError(true);
    } finally {
      setCatalogLoading(false);
    }
  }

  useEffect(() => {
    void loadCatalog();
    void (async () => {
      const startNavigationVersion = navigationVersionRef.current;
      try {
        const response = await fetch("/api/chat/sessions", { cache: "no-store" });
        const data = response.ok ? await response.json() as { sessions: StoredSession[] } : { sessions: [] };
        if (!response.ok && response.status !== 401) throw new Error("chat_store_unavailable");
        const initial = (Array.isArray(data.sessions) ? data.sessions : []).map(fromStoredSession);
        setSessions((current) => mergeLoadedSessions(initial, current));
        const preferred = new URLSearchParams(window.location.search).get("session");
        const selected = initial.find((session) => session.id === preferred);
        if (selected && shouldApplyInitialSession(preferred, startNavigationVersion,
          navigationVersionRef.current)) {
          setActiveSessionId(selected.id);
          await loadMessages(selected.id);
        }
      } catch {
        setComposerErrors({ "": "โหลดประวัติแชทจากฐานข้อมูลไม่ได้ กรุณารีเฟรชหน้า" });
      } finally { setSessionsReady(true); }
    })();
  }, []);

  async function createRemoteSession(): Promise<StoredSession> {
    const response = await fetch("/api/chat/sessions", { method: "POST" });
    if (!response.ok) throw new Error("chat_store_unavailable");
    return await response.json() as StoredSession;
  }

  async function loadMessages(sessionId: string) {
    const response = await fetch(`/api/chat/sessions/${sessionId}/messages`, { cache: "no-store" });
    if (!response.ok) throw new Error("chat_history_unavailable");
    const data = await response.json() as { messages: StoredMessage[]; has_more: boolean };
    setHasOlder((current) => ({ ...current, [sessionId]: data.has_more }));
    updateSession(sessionId, (current) => ({ ...current, messages: [GREETING, ...data.messages.map(fromStoredMessage)] }));
  }

  async function loadOlderMessages(sessionId: string) {
    if (loadingOlderRef.current || !hasOlder[sessionId]) return;
    const session = sessions.find((item) => item.id === sessionId);
    const oldest = session?.messages.find((message) => message.createdAt);
    const list = listRef.current;
    if (!oldest?.createdAt || !list) return;
    loadingOlderRef.current = true;
    const previousHeight = list.scrollHeight;
    try {
      const params = new URLSearchParams({ before_at: oldest.createdAt, before_id: oldest.id });
      const response = await fetch(`/api/chat/sessions/${sessionId}/messages?${params}`, { cache: "no-store" });
      if (!response.ok) throw new Error("chat_history_unavailable");
      const data = await response.json() as { messages: StoredMessage[]; has_more: boolean };
      preserveScrollRef.current = true;
      setHasOlder((current) => ({ ...current, [sessionId]: data.has_more }));
      updateSession(sessionId, (current) => {
        const known = new Set(current.messages.map((message) => message.id));
        return { ...current, messages: [current.messages[0],
          ...data.messages.filter((row) => !known.has(row.id)).map(fromStoredMessage),
          ...current.messages.slice(1)] };
      });
      requestAnimationFrame(() => { if (listRef.current === list) list.scrollTop += list.scrollHeight - previousHeight; });
    } catch {
      setComposerErrors((current) => ({ ...current, [sessionId]: "โหลดข้อความเก่าไม่ได้ ลองเลื่อนขึ้นอีกครั้ง" }));
    } finally { loadingOlderRef.current = false; }
  }

  const activeSession = sessions.find((session) => session.id === activeSessionId);
  const messages = activeSession?.messages ?? [GREETING];
  const draft = drafts[activeSessionId] ?? "";
  const sending = sendingSessions.has(activeSessionId);
  const composerError = composerErrors[activeSessionId] ?? "";
  const attachedFiles = mediaFiles[activeSessionId] ?? [];

  useEffect(() => {
    if (preserveScrollRef.current) { preserveScrollRef.current = false; return; }
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [activeSessionId, messages, sending]);

  const catalogStatus = getCatalogStatus(catalogLoading, catalogError);
  const activeProduct = [...messages].reverse().find((message) => message.productName)?.productName;
  const normalizedSessionSearch = sessionSearch.trim().toLocaleLowerCase();
  const visibleSessions = sessions.filter((session) => !normalizedSessionSearch ||
    `${session.title} ${session.messages.map((message) => message.text).join(" ")}`.toLocaleLowerCase().includes(normalizedSessionSearch));

  function updateSession(sessionId: string, update: (session: ChatSession) => ChatSession) {
    setSessions((current) => current.map((session) => session.id === sessionId ? update(session) : session));
  }

  function setSessionSending(sessionId: string, value: boolean) {
    setSendingSessions((current) => {
      const next = new Set(current);
      if (value) next.add(sessionId);
      else next.delete(sessionId);
      return next;
    });
  }

  async function startNewChat() {
    navigationVersionRef.current += 1;
    try {
      const session = fromStoredSession(await createRemoteSession());
      setSessions((current) => [session, ...current]);
      setActiveSessionId(session.id);
      window.history.replaceState(null, "", `?session=${session.id}`);
      setDrafts((current) => ({ ...current, [session.id]: "" }));
      setSessionsOpen(false);
      setKnowledgeOpen(false);
      setTimeout(() => textareaRef.current?.focus(), 0);
    } catch { setComposerErrors((current) => ({ ...current, [activeSessionId]: "สร้างแชทในฐานข้อมูลไม่ได้" })); }
  }

  function selectSession(sessionId: string) {
    navigationVersionRef.current += 1;
    setActiveSessionId(sessionId);
    window.history.replaceState(null, "", `?session=${sessionId}`);
    void loadMessages(sessionId).catch(() => setComposerErrors((current) => ({ ...current, [sessionId]: "โหลดประวัติแชทไม่ได้" })));
    setSessionsOpen(false);
    setKnowledgeOpen(false);
    setTimeout(() => textareaRef.current?.focus(), 0);
  }

  async function sendQuestion(event?: FormEvent) {
    event?.preventDefault();
    const session = sessions.find((item) => item.id === activeSessionId);
    if (!session || sendingSessions.has(session.id)) return;
    let payload: ReturnType<typeof buildSessionChatRequest>;
    try {
      payload = buildSessionChatRequest(session.id, draft || (itemIds[session.id] ? "ช่วยดูสินค้าจากการ์ดนี้" : mediaFiles[session.id]?.length ? "ช่วยดูภาพหรือวิดีโอนี้" : ""), []);
    } catch (error) {
      setComposerErrors((current) => ({ ...current, [session.id]: error instanceof Error ? error.message : "กรุณาตรวจสอบคำถามอีกครั้ง" }));
      return;
    }

    const question = payload.question;
    const itemId = itemIds[session.id]?.trim();
    const files = mediaFiles[session.id] || [];
    setDrafts((current) => ({ ...current, [session.id]: "" }));
    setItemIds((current) => ({ ...current, [session.id]: "" }));
    setMediaFiles((current) => ({ ...current, [session.id]: [] }));
    setComposerErrors((current) => ({ ...current, [session.id]: "" }));
    updateSession(session.id, (current) => ({
      ...current,
      title: current.title === "แชทใหม่" ? titleFromQuestion(question) : current.title,
      updatedAt: Date.now(),
      messages: [...current.messages, { id: crypto.randomUUID(), role: "user", kind: "answer", text: question }],
    }));
    setSessionSending(session.id, true);

    try {
      const attachmentIds: string[] = [];
      for (const file of files) {
        const signedResponse = await fetch("/api/chat/media/upload-url", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: session.id, filename: file.name,
            mime_type: file.type, byte_size: file.size }),
        });
        if (!signedResponse.ok) throw new Error("media_upload_unavailable");
        const signed = await signedResponse.json() as { attachment_id: string; upload_url: string };
        const uploaded = await fetch(signed.upload_url, { method: "PUT", headers: { "Content-Type": file.type }, body: file });
        if (!uploaded.ok) throw new Error("media_upload_failed");
        attachmentIds.push(signed.attachment_id);
      }
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payload, attachment_ids: attachmentIds,
          ...(itemId ? { event: { type: "item", item_id: itemId, text: draft } } : {}) }),
      });
      if (!response.ok) throw new Error("chat_unavailable");
      const reply = await response.json() as ChatReply;
      const presented = presentChatReply(reply);
      const analysis = reply.intent_analysis && typeof reply.intent_analysis === "object"
        ? reply.intent_analysis as Record<string, unknown> : {};
      const productNames = Array.isArray(analysis.product_names)
        ? analysis.product_names.filter((name): name is string => typeof name === "string") : [];
      updateSession(session.id, (current) => ({
        ...current,
        updatedAt: Date.now(),
        messages: [...current.messages, {
          id: crypto.randomUUID(),
          role: "assistant",
          kind: presented.kind,
          text: presented.answer,
          sources: presented.sources,
          productNames,
          handoffSent: presented.handoffSent,
          ...(presented.productName ? { productName: presented.productName } : {}),
          ...(presented.caseId !== undefined ? { caseId: presented.caseId } : {}),
        }],
      }));
      await loadMessages(session.id);
    } catch {
      updateSession(session.id, (current) => ({
        ...current,
        messages: [...current.messages, {
          id: crypto.randomUUID(),
          role: "assistant",
          kind: "error",
          text: "เชื่อมต่อระบบตอบคำถามไม่ได้ ลองส่งอีกครั้งครับ",
          sources: [],
        }],
      }));
    } finally {
      setSessionSending(session.id, false);
      if (activeSessionId === session.id) textareaRef.current?.focus();
    }
  }

  function handleComposerKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendQuestion();
    }
  }

  return (
    <div className={`app-shell ${sessionsOpen ? "settings-open" : ""}`}>
      <main className="page-wrap">
        <div className="chat-workspace">
          <aside className="setup-panel" aria-label="รายการแชท">
            <div className="setup-title-row">
              <div className="section-icon"><MessageCircle size={18} strokeWidth={1.8} /></div>
              <h2>บทสนทนา</h2>
              <span className="session-total">{sessions.length}</span>
              <button className="session-add-button" type="button" onClick={startNewChat} aria-label="เริ่มแชทใหม่">
                <Plus size={17} strokeWidth={2} />
              </button>
              <button className="settings-close" type="button" onClick={() => setSessionsOpen(false)} aria-label="ปิดรายการแชท">
                <X size={16} />
              </button>
            </div>

            <label className="session-search"><Search size={15} /><input type="search" value={sessionSearch} onChange={(event) => setSessionSearch(event.target.value)} placeholder="ค้นหาบทสนทนา" aria-label="ค้นหาบทสนทนา" /></label>

            <div className="session-list" role="list" aria-label="แชทที่บันทึกไว้">
              {!sessionsReady && <p className="field-help">กำลังโหลดแชท...</p>}
              {visibleSessions.map((session) => {
                const preview = [...session.messages].reverse().find((message) => message.role === "user")?.text || "เริ่มบทสนทนาใหม่";
                const updatedAt = Number.isNaN(session.updatedAt) ? "" : new Intl.DateTimeFormat("th-TH", { hour: "2-digit", minute: "2-digit" }).format(session.updatedAt);
                return (
                  <div key={session.id} role="listitem">
                    <button
                      className={`session-item ${session.id === activeSessionId ? "is-active" : ""}`}
                      type="button"
                      onClick={() => selectSession(session.id)}
                      aria-current={session.id === activeSessionId ? "page" : undefined}
                    >
                      <span className="session-item-avatar"><MessageCircle size={15} /></span>
                      <span className="session-item-copy">
                        <span className="session-item-line"><span className="session-item-title">{session.title}</span><time>{updatedAt}</time></span>
                        <span className="session-item-preview">{preview}</span>
                        <span className="session-item-status">{sendingSessions.has(session.id) ? <><LoaderCircle size={12} className="spin" /> กำลังตอบ</> : `${session.messages.filter((message) => message.role === "user").length} ข้อความ`}</span>
                      </span>
                    </button>
                  </div>
                );
              })}
              {sessionsReady && visibleSessions.length === 0 && <p className="session-empty">ไม่พบบทสนทนาที่ค้นหา</p>}
            </div>
          </aside>

          <section className="conversation-panel" aria-label="แชทถามตอบสินค้า">
            {activeSession ? <>
            <div className="conversation-header">
              <div className="conversation-identity">
                <div className="bot-avatar"><Bot size={18} strokeWidth={1.8} /></div>
                <div>
                  <h2>{activeSession?.title || "ผู้ช่วย Black Shark"}</h2>
                  <p className="conversation-status">
                    <span className={`online-dot is-${catalogStatus}`} />
                    ผู้ช่วย Black Shark · {catalogStatus === "ready" ? "พร้อมค้นข้อมูล" : catalogStatus === "loading" ? "กำลังโหลดข้อมูลสินค้า" : "เชื่อมต่อฐานข้อมูลไม่ได้"}
                    <span className="status-divider">·</span>
                    <span className="selected-product" title={activeProduct || "รุ่นจะตรวจจากบริบทแชท"}>{activeProduct || "ตรวจรุ่นจากแชทนี้"}</span>
                  </p>
                </div>
              </div>
              <button className="sessions-toggle-button" type="button" onClick={() => setSessionsOpen(true)} aria-label="เปิดรายการแชท">
                <MessageCircle size={16} strokeWidth={1.8} /><span>แชท</span>
              </button>
              <button className="new-chat-button" type="button" onClick={startNewChat} aria-label="เริ่มแชทใหม่">
                <MessageCircle size={16} strokeWidth={1.8} /><span>เริ่มใหม่</span>
              </button>
              <button className="conversation-info-toggle" type="button" onClick={() => setKnowledgeOpen((open) => !open)} aria-expanded={knowledgeOpen} aria-label="เปิดข้อมูลแชท">
                <Info size={16} strokeWidth={1.8} /><span>ข้อมูล</span>
              </button>
            </div>

            <div className="message-list" ref={listRef} role="log" aria-live="polite" aria-relevant="additions text"
              onScroll={(event) => { if (event.currentTarget.scrollTop < 80) void loadOlderMessages(activeSession.id); }}>
              {messages.map((message) => (
                <article key={message.id} className={`message-row ${message.role === "user" ? "from-user" : "from-assistant"}`}>
                  {message.role === "assistant" && (
                    <div className={`message-avatar ${message.kind === "handoff" ? "is-handoff" : ""}`}>
                      {message.kind === "handoff" ? <Headphones size={15} /> : <Bot size={15} />}
                    </div>
                  )}
                  <div className="message-body">
                    {message.kind === "handoff" && <div className="message-state handoff-state"><Headphones size={13} /> {message.handoffSent ? "ส่งต่อเจ้าหน้าที่แล้ว" : "จำลองส่งต่อเจ้าหน้าที่"}</div>}
                    {message.kind === "need_model" && <div className="message-state question-state"><CircleHelp size={13} /> ขอข้อมูลเพิ่ม</div>}
                    {message.kind === "setup_required" && <div className="message-state error-state"><ShieldCheck size={13} /> ต้องตั้งค่า API key</div>}
                    {message.kind === "error" && <div className="message-state error-state"><CircleHelp size={13} /> เชื่อมต่อไม่สำเร็จ</div>}
                    <div className={`message-bubble ${message.role === "user" ? "user-bubble" : "assistant-bubble"} ${message.kind === "error" ? "error-bubble" : ""}`}>
                      {message.role === "assistant" ? parseChatAnswer(message.text).map((block, index) =>
                        block.type === "text" ? <p key={index}>{renderLinkedText(block.text)}</p> : (
                          <div className="chat-answer-table-wrap" key={index}>
                            <table className="chat-answer-table" aria-label="ตารางเปรียบเทียบสินค้า">
                              <thead><tr>{block.headers.map((header, cell) => <th scope="col" key={cell}>{header}</th>)}</tr></thead>
                              <tbody>{block.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) =>
                                <td key={cellIndex}>{cell}</td>)}</tr>)}</tbody>
                            </table>
                          </div>
                        )) : <p>{message.text}</p>}
                    </div>
                    {message.kind === "setup_required" && <a className="message-action" href="/llm">ไปตั้งค่าโมเดลและ API keys</a>}
                    {message.kind === "handoff" && message.handoffSent && (
                      <p className="handoff-note">ส่งเรื่องให้เจ้าหน้าที่จริงแล้ว{message.caseId !== undefined ? ` · เคส #${message.caseId}` : ""} ทีมงานจะติดต่อกลับ</p>
                    )}
                    {message.kind === "handoff" && !message.handoffSent && (
                      <p className="handoff-note">บันทึกคำขอไว้ในระบบแล้ว ยังไม่มีการส่งข้อความถึงเจ้าหน้าที่ผ่าน Shopee</p>
                    )}
                    {!!message.productCards?.length ? (
                      <div className="message-product-cards">
                        {message.productCards.map((card, index) => (
                          <div className="message-product-card" key={`${card.itemId}-${index}`}>
                            {card.imageUrl && <img src={card.imageUrl} alt={card.title} />}
                            <div><strong>{card.title}</strong><span>{card.itemId ? `Shopee item_id ${card.itemId}` : "สินค้าที่ส่งมา"}</span></div>
                          </div>
                        ))}
                      </div>
                    ) : message.cardTitle ? (
                      <div className="message-product-card">
                        {message.imageUrl && <img src={message.imageUrl} alt={message.cardTitle} />}
                        <div><strong>{message.cardTitle}</strong><span>การ์ดสินค้าที่ส่งมา</span></div>
                      </div>
                    ) : null}
                    {message.imageUrl && !message.cardTitle && <img src={message.imageUrl} alt={message.productName || "รูปสินค้า"} className="message-product-image" />}
                    {!!message.attachments?.length && (
                      <div className="message-attachments">
                        {message.attachments.map((attachmentId) => <MediaAttachment key={attachmentId} sessionId={activeSessionId} attachmentId={attachmentId} />)}
                      </div>
                    )}
                    {!!message.sources?.length && (
                      <div className="sources-list" aria-label="แหล่งข้อมูลอ้างอิง">
                        {message.sources.map((source, index) => (
                          <div className="source-row" key={`${source.file}-${source.sheet}-${source.cell}-${index}`}>
                            <FileText size={14} strokeWidth={1.8} />
                            <span className="source-file" title={source.file}>{source.file}</span>
                            {(source.sheet || source.cell) && (
                              <span className="source-location">{[source.sheet, source.cell].filter(Boolean).join(" · ")}</span>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </article>
              ))}

              {sending && (
                <div className="thinking-row" role="status">
                  <div className="message-avatar"><Bot size={15} /></div>
                  <div className="thinking-bubble"><LoaderCircle size={16} className="spin" /> กำลังค้นจากข้อมูลที่มี</div>
                </div>
              )}
            </div>

            <form className="composer" onSubmit={(event) => void sendQuestion(event)}>
              <label className="upload-control">
                <input type="file" aria-label="แนบภาพหรือวิดีโอ" accept="image/jpeg,image/png,image/webp,image/gif,video/mp4,video/webm,video/quicktime"
                  multiple disabled={!activeSession || sending}
                  onChange={(event) => {
                    const selected = Array.from(event.currentTarget.files || []);
                    const current = mediaFiles[activeSessionId] || [];
                    const remaining = Math.max(0, 10 - current.length);
                    const accepted = selected.slice(0, remaining);
                    setMediaFiles((files) => ({ ...files, [activeSessionId]: [...(files[activeSessionId] || []), ...accepted] }));
                    setComposerErrors((errors) => ({ ...errors, [activeSessionId]: selected.length > remaining ? "แนบได้สูงสุด 10 ไฟล์ต่อคำถาม" : "" }));
                    event.currentTarget.value = "";
                  }} />
                <span className="upload-control-button"><Paperclip size={16} strokeWidth={1.9} /> แนบภาพหรือวิดีโอ</span>
                <span className={`upload-control-status ${attachedFiles.length ? "has-files" : ""}`} title={attachedFiles.map((file) => file.name).join(", ")}>
                  {attachedFiles.length ? `${attachedFiles[0].name}${attachedFiles.length > 1 ? ` +${attachedFiles.length - 1} ไฟล์` : ""}` : "JPG · PNG · MP4 · สูงสุด 10 ไฟล์"}
                </span>
              </label>
              {!!mediaFiles[activeSessionId]?.length && <div className="composer-media-count" role="status">
                แนบแล้ว {mediaFiles[activeSessionId].length}/10 ไฟล์
                <button type="button" onClick={() => setMediaFiles((current) => ({ ...current, [activeSessionId]: [] }))}>ล้างไฟล์</button>
              </div>}
              <input type="text" inputMode="numeric" value={itemIds[activeSessionId] || ""}
                onChange={(event) => setItemIds((current) => ({ ...current, [activeSessionId]: event.target.value }))}
                placeholder="item_id จากการ์ดสินค้า (ถ้ามี)" aria-label="item_id จากการ์ดสินค้า"
                maxLength={100} disabled={!activeSession || sending} />
              <div className="composer-main">
                <textarea
                  ref={textareaRef}
                  value={draft}
                  onChange={(event) => {
                    setDrafts((current) => ({ ...current, [activeSessionId]: event.target.value }));
                    setComposerErrors((current) => ({ ...current, [activeSessionId]: "" }));
                  }}
                  onKeyDown={handleComposerKey}
                  placeholder="พิมพ์คำถามได้เลย เช่น รุ่นนี้แบตเตอรี่กี่ mAh?"
                  aria-label="พิมพ์คำถาม"
                  rows={1}
                  maxLength={1000}
                  disabled={!sessionsReady || catalogLoading || catalogError || !activeSession}
                />
                <button className="send-button" type="submit" disabled={sending || (!draft.trim() && !itemIds[activeSessionId]?.trim() && !mediaFiles[activeSessionId]?.length) || !sessionsReady || catalogLoading || catalogError} aria-label="ส่งคำถาม">
                  {sending ? <LoaderCircle size={18} className="spin" /> : <ArrowUp size={19} strokeWidth={2.1} />}
                </button>
              </div>
              {composerError && <p className="composer-error" role="alert">{composerError}</p>}
              <div className="composer-footer">
                <span><ShieldCheck size={13} /> ใช้ข้อมูลจากเอกสารที่มีเท่านั้น</span>
                <span><kbd>Enter</kbd> ส่ง · <kbd>Shift</kbd> + <kbd>Enter</kbd> ขึ้นบรรทัดใหม่</span>
              </div>
            </form>
            </> : <div className="conversation-empty" role="status" aria-live="polite">
              <div className="conversation-empty-mark"><UserRound size={25} strokeWidth={1.8} /></div>
              <h2>{sessionsReady ? "เลือกบทสนทนาเพื่อดูรายละเอียด" : "กำลังโหลดบทสนทนา"}</h2>
              <p>{sessions.length ? "แชทและข้อความจะแสดงที่นี่เมื่อเลือกจากรายการ" : "เริ่มบทสนทนาใหม่เพื่อทดลองผู้ช่วยข้อมูลสินค้า"}</p>
              {!sessions.length && sessionsReady && <button className="empty-new-chat" type="button" onClick={startNewChat}><MessageCircle size={15} /> เริ่มบทสนทนาใหม่</button>}
              <button className="empty-sessions-toggle" type="button" onClick={() => setSessionsOpen(true)}>ดูรายการบทสนทนา</button>
            </div>}
          </section>

          {knowledgeOpen && <aside className="info-panel" aria-label="ข้อมูลแชทและฐานความรู้">
            <div className="info-title-row">
              <div className="section-icon"><Info size={18} strokeWidth={1.8} /></div>
              <div>
                <h2>ข้อมูลแชท</h2>
                <p>สถานะและข้อมูลที่ใช้ตอบ</p>
              </div>
              <button className="info-close" type="button" onClick={() => setKnowledgeOpen(false)} aria-label="ปิดข้อมูลแชท"><X size={16} /></button>
            </div>

            <div className="info-status" role="status">
              <span className={`online-dot is-${catalogStatus}`} />
              <span>{catalogStatus === "ready" ? "ฐานข้อมูลพร้อมค้น" : catalogStatus === "loading" ? "กำลังโหลดข้อมูลสินค้า" : "เชื่อมต่อฐานข้อมูลไม่ได้"}</span>
            </div>

            <div className="source-assurance"><ShieldCheck size={16} strokeWidth={1.8} /><span>ตอบจากข้อมูลต้นทางเท่านั้น</span></div>

            {catalogError ? (
              <div className="catalog-error" role="alert">
                <p>โหลดรายการสินค้าไม่ได้</p>
                <button type="button" onClick={() => void loadCatalog()}><RefreshCw size={14} /> ลองอีกครั้ง</button>
              </div>
            ) : (
              <div className="scope-rule">
                <div className="scope-rule-icon"><Check size={14} strokeWidth={2.4} /></div>
                <p>จับรุ่นจากข้อความในแชทนี้ ถ้าคลุมเครือจะถามกลับก่อนค้นข้อมูล</p>
              </div>
            )}

            <div className="knowledge-counts" aria-label="จำนวนข้อมูลที่โหลดได้">
              <div><span>สินค้า</span><strong>{catalog?.counts.products ?? "—"}</strong></div>
              <div><span>รุ่นที่มีคู่มือ</span><strong>{catalog?.counts.support_models ?? "—"}</strong></div>
              <div><span>หัวข้อแก้ปัญหา</span><strong>{catalog?.counts.faq_entries ?? "—"}</strong></div>
            </div>

            <div className="info-storage-note">
              <ShieldCheck size={15} strokeWidth={1.8} />
              <p>ประวัติแต่ละแชทแยกกันและบันทึกไว้ในฐานข้อมูล</p>
            </div>
          </aside>}
        </div>
      </main>
      {sessionsOpen && <button className="settings-backdrop" type="button" aria-label="ปิดรายการแชท" onClick={() => setSessionsOpen(false)} />}
    </div>
  );
}
