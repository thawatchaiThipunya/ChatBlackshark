"use client";

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
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
  RefreshCw,
  ShieldCheck,
  X,
} from "lucide-react";
import { buildSessionChatRequest, getCatalogStatus, presentChatReply, type ChatSource } from "@/lib/chat-utils";

type Catalog = {
  products: Array<{ name: string }>;
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
  handoffSent?: boolean;
  caseId?: number;
};
type ChatSession = { id: string; title: string; updatedAt: number; messages: ChatMessage[] };

const STORAGE_KEY = "black-shark-chat-sessions-v1";
const ACTIVE_SESSION_KEY = "black-shark-active-session-v1";
const GREETING: ChatMessage = {
  id: "welcome",
  role: "assistant",
  kind: "welcome",
  text: "สวัสดีครับ พิมพ์คำถามได้เลย ไม่ต้องเลือกสินค้าก่อน ผมจะจับรุ่นจากข้อความและบริบทในแชทนี้ครับ",
};

function createSession(): ChatSession {
  return { id: crypto.randomUUID(), title: "แชทใหม่", updatedAt: Date.now(), messages: [GREETING] };
}

function isChatSession(value: unknown): value is ChatSession {
  if (typeof value !== "object" || value === null) return false;
  const item = value as Record<string, unknown>;
  return typeof item.id === "string" && typeof item.title === "string" &&
    typeof item.updatedAt === "number" && Array.isArray(item.messages);
}

function readSessions(): ChatSession[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    return Array.isArray(saved) ? saved.filter(isChatSession).slice(-40) : [];
  } catch {
    return [];
  }
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
  const [sendingSessions, setSendingSessions] = useState<Set<string>>(new Set());
  const [composerErrors, setComposerErrors] = useState<Record<string, string>>({});
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

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
    const stored = readSessions();
    const initial = stored.length ? stored : [createSession()];
    setSessions(initial);
    const preferred = sessionStorage.getItem(ACTIVE_SESSION_KEY);
    setActiveSessionId(initial.some((session) => session.id === preferred) ? preferred! : initial[0].id);
    setSessionsReady(true);
  }, []);

  useEffect(() => {
    if (!sessionsReady) return;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
  }, [sessions, sessionsReady]);

  useEffect(() => {
    if (sessionsReady && activeSessionId) sessionStorage.setItem(ACTIVE_SESSION_KEY, activeSessionId);
  }, [activeSessionId, sessionsReady]);

  const activeSession = sessions.find((session) => session.id === activeSessionId);
  const messages = activeSession?.messages ?? [GREETING];
  const draft = drafts[activeSessionId] ?? "";
  const sending = sendingSessions.has(activeSessionId);
  const composerError = composerErrors[activeSessionId] ?? "";

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [activeSessionId, messages, sending]);

  const catalogStatus = getCatalogStatus(catalogLoading, catalogError);
  const activeProduct = [...messages].reverse().find((message) => message.productName)?.productName;

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

  function startNewChat() {
    const session = createSession();
    setSessions((current) => [...current, session]);
    setActiveSessionId(session.id);
    setDrafts((current) => ({ ...current, [session.id]: "" }));
    setSessionsOpen(false);
    setTimeout(() => textareaRef.current?.focus(), 0);
  }

  function selectSession(sessionId: string) {
    setActiveSessionId(sessionId);
    setSessionsOpen(false);
    setTimeout(() => textareaRef.current?.focus(), 0);
  }

  async function sendQuestion(event?: FormEvent) {
    event?.preventDefault();
    const session = sessions.find((item) => item.id === activeSessionId);
    if (!session || sendingSessions.has(session.id)) return;
    let payload: ReturnType<typeof buildSessionChatRequest>;
    try {
      const history = session.messages
        .filter((message) => message.kind !== "welcome" && message.kind !== "error")
        .map((message) => ({
          role: message.role,
          content: message.text,
          ...(message.productName ? { product_name: message.productName } : {}),
        } as const))
        .slice(-12);
      payload = buildSessionChatRequest(session.id, draft, history);
    } catch (error) {
      setComposerErrors((current) => ({ ...current, [session.id]: error instanceof Error ? error.message : "กรุณาตรวจสอบคำถามอีกครั้ง" }));
      return;
    }

    const question = payload.question;
    setDrafts((current) => ({ ...current, [session.id]: "" }));
    setComposerErrors((current) => ({ ...current, [session.id]: "" }));
    updateSession(session.id, (current) => ({
      ...current,
      title: current.title === "แชทใหม่" ? titleFromQuestion(question) : current.title,
      updatedAt: Date.now(),
      messages: [...current.messages, { id: crypto.randomUUID(), role: "user", kind: "answer", text: question }],
    }));
    setSessionSending(session.id, true);

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error("chat_unavailable");
      const presented = presentChatReply(await response.json());
      updateSession(session.id, (current) => ({
        ...current,
        updatedAt: Date.now(),
        messages: [...current.messages, {
          id: crypto.randomUUID(),
          role: "assistant",
          kind: presented.kind,
          text: presented.answer,
          sources: presented.sources,
          handoffSent: presented.handoffSent,
          ...(presented.productName ? { productName: presented.productName } : {}),
          ...(presented.caseId !== undefined ? { caseId: presented.caseId } : {}),
        }],
      }));
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
      <header className="topbar">
        <div className="brand-block">
          <a className="brand" href="/" aria-label="Black Shark หน้าหลัก">
            <span className="brand-mark" aria-hidden="true"><span /></span>
          </a>
          <div className="brand-copy">
            <h1>ทดสอบบอท — Black Shark</h1>
            <p><span className={`online-dot is-${catalogStatus}`} /> {catalogStatus === "loading" ? "กำลังโหลดข้อมูล" : catalogStatus === "offline" ? "ยังเชื่อมต่อไม่ได้" : "ผู้ช่วยข้อมูลสินค้า"}</p>
          </div>
        </div>
        <div className="topbar-actions">
          <a className="llm-link" href="/llm">จัดการ Gemini key</a>
          <button className="topbar-settings" type="button" onClick={() => setSessionsOpen(true)} aria-label="เปิดรายการแชท" title="รายการแชท">
            <MessageCircle size={17} />
          </button>
        </div>
      </header>

      <main className="page-wrap">
        <nav className="mode-switcher-bar" aria-label="ขอบเขตการตอบ">
          <strong className="unified-chat-label">ถามข้อมูลสินค้าได้เลย ไม่ต้องเลือกสินค้าก่อน</strong>
        </nav>

        <div className="chat-workspace">
          <aside className="setup-panel" aria-label="รายการแชท">
            <div className="setup-title-row">
              <div className="section-icon"><MessageCircle size={18} strokeWidth={1.8} /></div>
              <h2>บทสนทนา</h2>
              <button className="settings-close" type="button" onClick={() => setSessionsOpen(false)} aria-label="ปิดรายการแชท">
                <X size={16} />
              </button>
            </div>

            <button className="new-chat-button session-create-button" type="button" onClick={startNewChat}>
              <MessageCircle size={15} strokeWidth={1.8} /><span>เริ่มแชทใหม่</span>
            </button>

            <div className="session-list" role="list" aria-label="แชทที่บันทึกไว้">
              {!sessionsReady && <p className="field-help">กำลังโหลดแชท...</p>}
              {sessions.map((session) => (
                <div key={session.id} role="listitem">
                  <button
                    className={`session-item ${session.id === activeSessionId ? "is-active" : ""}`}
                    type="button"
                    onClick={() => selectSession(session.id)}
                    aria-current={session.id === activeSessionId ? "page" : undefined}
                  >
                    <span className="session-item-title">{session.title}</span>
                    <span className="session-item-status">
                      {sendingSessions.has(session.id) ? <><LoaderCircle size={12} className="spin" /> กำลังตอบ</> : `${session.messages.filter((message) => message.role === "user").length} คำถาม`}
                    </span>
                  </button>
                </div>
              ))}
            </div>
          </aside>

          <section className="conversation-panel" aria-label="แชทถามตอบสินค้า">
            <div className="conversation-header">
              <div className="conversation-identity">
                <div className="bot-avatar"><Bot size={18} strokeWidth={1.8} /></div>
                <div>
                  <h2>ผู้ช่วย Black Shark</h2>
                  <p className="conversation-status">
                    <span className={`online-dot is-${catalogStatus}`} />
                    {catalogStatus === "ready" ? "พร้อมค้นข้อมูล" : catalogStatus === "loading" ? "กำลังโหลดข้อมูลสินค้า" : "เชื่อมต่อฐานข้อมูลไม่ได้"}
                    <span className="status-divider">·</span>
                    <span className="selected-product" title={activeProduct || "รุ่นจะตรวจจากบริบทแชท"}>{activeProduct || "ตรวจรุ่นจากแชทนี้"}</span>
                  </p>
                </div>
              </div>
              <button className="new-chat-button" type="button" onClick={startNewChat} aria-label="เริ่มแชทใหม่">
                <MessageCircle size={16} strokeWidth={1.8} /><span>เริ่มใหม่</span>
              </button>
            </div>

            <div className="message-list" ref={listRef} role="log" aria-live="polite" aria-relevant="additions text">
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
                    {message.kind === "setup_required" && <div className="message-state error-state"><ShieldCheck size={13} /> ต้องตั้งค่า Gemini</div>}
                    {message.kind === "error" && <div className="message-state error-state"><CircleHelp size={13} /> เชื่อมต่อไม่สำเร็จ</div>}
                    <div className={`message-bubble ${message.role === "user" ? "user-bubble" : "assistant-bubble"} ${message.kind === "error" ? "error-bubble" : ""}`}>
                      <p>{message.text}</p>
                    </div>
                    {message.kind === "setup_required" && <a className="message-action" href="/llm">ไปที่จัดการ Gemini key</a>}
                    {message.kind === "handoff" && message.handoffSent && (
                      <p className="handoff-note">ส่งเรื่องให้เจ้าหน้าที่จริงแล้ว{message.caseId !== undefined ? ` · เคส #${message.caseId}` : ""} ทีมงานจะติดต่อกลับ</p>
                    )}
                    {message.kind === "handoff" && !message.handoffSent && (
                      <p className="handoff-note">นี่คือการจำลองเท่านั้น ยังไม่มีการส่งข้อความถึงเจ้าหน้าที่จริง</p>
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
                <button className="send-button" type="submit" disabled={sending || !draft.trim() || !sessionsReady || catalogLoading || catalogError} aria-label="ส่งคำถาม">
                  {sending ? <LoaderCircle size={18} className="spin" /> : <ArrowUp size={19} strokeWidth={2.1} />}
                </button>
              </div>
              {composerError && <p className="composer-error" role="alert">{composerError}</p>}
              <div className="composer-footer">
                <span><ShieldCheck size={13} /> ใช้ข้อมูลจากเอกสารที่มีเท่านั้น</span>
                <span><kbd>Enter</kbd> ส่ง · <kbd>Shift</kbd> + <kbd>Enter</kbd> ขึ้นบรรทัดใหม่</span>
              </div>
            </form>
          </section>

          <aside className="info-panel" aria-label="ข้อมูลแชทและฐานความรู้">
            <div className="info-title-row">
              <div className="section-icon"><Info size={18} strokeWidth={1.8} /></div>
              <div>
                <h2>ข้อมูลแชท</h2>
                <p>สถานะและข้อมูลที่ใช้ตอบ</p>
              </div>
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
              <p>ประวัติแต่ละแชทแยกกันและเก็บไว้ใน browser นี้</p>
            </div>
          </aside>
        </div>
      </main>
      {sessionsOpen && <button className="settings-backdrop" type="button" aria-label="ปิดรายการแชท" onClick={() => setSessionsOpen(false)} />}
    </div>
  );
}
