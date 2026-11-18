"use client";

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import {
  ArrowUp,
  Bot,
  Check,
  ChevronDown,
  CircleHelp,
  FileText,
  Headphones,
  LoaderCircle,
  MessageCircle,
  PackageSearch,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Wrench,
  X,
} from "lucide-react";
import { buildChatRequest, getCatalogStatus, presentChatReply, type ChatMode, type ChatSource } from "@/lib/chat-utils";

type Catalog = {
  products: Array<{ name: string }>;
  support_models: string[];
  counts: { products: number; support_models: number; faq_entries: number };
};

type MessageKind = "welcome" | "answer" | "handoff" | "need_model" | "error";
type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  kind: MessageKind;
  text: string;
  sources?: ChatSource[];
  handoffSent?: boolean;
};

const GREETING: ChatMessage = {
  id: "welcome",
  role: "assistant",
  kind: "welcome",
  text: "สวัสดีครับ เลือกสินค้า หรือรุ่นที่ต้องการ แล้วพิมพ์คำถามได้เลยครับ",
};

export default function HomePage() {
  const [mode, setMode] = useState<ChatMode>("spec");
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState(false);
  const [selectedName, setSelectedName] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([GREETING]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [composerError, setComposerError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  async function loadCatalog() {
    setCatalogLoading(true);
    setCatalogError(false);
    try {
      const response = await fetch("/api/catalog", { cache: "no-store" });
      if (!response.ok) throw new Error("catalog_unavailable");
      const data = (await response.json()) as Catalog;
      setCatalog(data);
      setSelectedName((current) => current || data.products[0]?.name || data.support_models[0] || "");
    } catch {
      setCatalogError(true);
    } finally {
      setCatalogLoading(false);
    }
  }

  useEffect(() => {
    void loadCatalog();
  }, []);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, sending]);

  const options = mode === "spec" ? catalog?.products.map((product) => product.name) ?? [] : catalog?.support_models ?? [];
  const currentModeLabel = mode === "spec" ? "ข้อมูลสินค้า" : "แก้ปัญหาเบื้องต้น";
  const catalogStatus = getCatalogStatus(catalogLoading, catalogError);

  function changeMode(nextMode: ChatMode) {
    setMode(nextMode);
    const nextOptions = nextMode === "spec" ? catalog?.products.map((product) => product.name) ?? [] : catalog?.support_models ?? [];
    setSelectedName(nextOptions[0] ?? "");
    setComposerError("");
  }

  async function sendQuestion(event?: FormEvent) {
    event?.preventDefault();
    if (sending) return;
    let payload: ReturnType<typeof buildChatRequest>;
    try {
      payload = buildChatRequest(mode, selectedName, draft);
    } catch (error) {
      setComposerError(error instanceof Error ? error.message : "กรุณาตรวจสอบคำถามอีกครั้ง");
      return;
    }

    const question = payload.question;
    setMessages((current) => [...current, { id: crypto.randomUUID(), role: "user", kind: "answer", text: question }]);
    setDraft("");
    setComposerError("");
    setSending(true);

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error("chat_unavailable");
      const presented = presentChatReply(await response.json());
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          kind: presented.kind,
          text: presented.answer,
          sources: presented.sources,
          handoffSent: presented.handoffSent,
        },
      ]);
    } catch {
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          kind: "error",
          text: "เชื่อมต่อระบบตอบคำถามไม่ได้ ลองตรวจว่า Python API ทำงานอยู่ แล้วส่งคำถามอีกครั้งครับ",
          sources: [],
        },
      ]);
    } finally {
      setSending(false);
      textareaRef.current?.focus();
    }
  }

  function handleComposerKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendQuestion();
    }
  }

  function startNewChat() {
    setMessages([GREETING]);
    setDraft("");
    setComposerError("");
    textareaRef.current?.focus();
  }

  return (
    <div className={`app-shell ${settingsOpen ? "settings-open" : ""}`}>
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
          <button className="topbar-settings" type="button" onClick={() => setSettingsOpen(true)} aria-label="เปิดการตั้งค่าสินค้า" title="สินค้าและข้อมูล">
            <Settings2 size={17} />
          </button>
        </div>
      </header>

      <main className="page-wrap">
        <nav className="mode-switcher-bar" aria-label="ประเภทแชท">
          <div className="mode-switch" role="tablist" aria-label="ประเภทคำถาม">
            <button
              className={`mode-button ${mode === "spec" ? "is-active" : ""}`}
              type="button"
              role="tab"
              aria-selected={mode === "spec"}
              disabled={catalogLoading || !catalog || sending}
              onClick={() => changeMode("spec")}
            >
              <PackageSearch size={16} /> ข้อมูลสินค้า
            </button>
            <button
              className={`mode-button ${mode === "troubleshooting" ? "is-active" : ""}`}
              type="button"
              role="tab"
              aria-selected={mode === "troubleshooting"}
              disabled={catalogLoading || !catalog || sending}
              onClick={() => changeMode("troubleshooting")}
            >
              <Wrench size={16} /> แก้ปัญหาเบื้องต้น
            </button>
          </div>
          <div className="source-assurance"><ShieldCheck size={16} strokeWidth={1.8} /><span>ตอบจากข้อมูลต้นทางเท่านั้น</span></div>
        </nav>

        <div className="chat-workspace">
          <aside className="setup-panel" aria-label="ตั้งค่าคำถาม">
            <div className="setup-title-row">
              <div className="section-icon"><PackageSearch size={18} strokeWidth={1.8} /></div>
              <h2>ข้อมูลสินค้า</h2>
              <button className="settings-close" type="button" onClick={() => setSettingsOpen(false)} aria-label="ปิดการตั้งค่า">
                <X size={16} />
              </button>
            </div>

            <label className="field-label" htmlFor="product-select">
              {mode === "spec" ? "สินค้า" : "เลือกรุ่นสินค้า"}
            </label>
            <div className="select-wrap">
              <select
                id="product-select"
                value={selectedName}
                onChange={(event) => setSelectedName(event.target.value)}
                disabled={catalogLoading || catalogError || options.length === 0 || sending}
              >
                {options.length === 0 && <option value="">{catalogLoading ? "กำลังโหลดรายการ..." : "ไม่มีรายการให้เลือก"}</option>}
                {options.map((option) => <option key={option} value={option}>{option}</option>)}
              </select>
              <ChevronDown size={17} aria-hidden="true" />
            </div>

            {catalogError ? (
              <div className="catalog-error" role="alert">
                <p>โหลดรายการสินค้าไม่ได้</p>
                <button type="button" onClick={() => void loadCatalog()}><RefreshCw size={14} /> ลองอีกครั้ง</button>
              </div>
            ) : (
              <p className="field-help">
                {mode === "spec"
                  ? "ถามข้อมูลหรือสเปกของสินค้าที่เลือก"
                  : "บอทจะค้นคู่มือของรุ่นที่เลือกก่อนตอบ"}
              </p>
            )}

            <div className="scope-rule">
              <div className="scope-rule-icon"><Check size={14} strokeWidth={2.4} /></div>
              <p>หากไม่มีข้อมูลที่ตรง ระบบจะแจ้งจำลองส่งต่อเจ้าหน้าที่</p>
            </div>

            <div className="knowledge-counts" aria-label="จำนวนข้อมูลที่โหลดได้">
              <div><span>สินค้า</span><strong>{catalog?.counts.products ?? "—"}</strong></div>
              <div><span>รุ่นที่มีคู่มือ</span><strong>{catalog?.counts.support_models ?? "—"}</strong></div>
              <div><span>หัวข้อแก้ปัญหา</span><strong>{catalog?.counts.faq_entries ?? "—"}</strong></div>
            </div>

            <div className="setup-bottom">
              <div className="secure-mark"><ShieldCheck size={16} strokeWidth={1.8} /></div>
              <p>คำตอบมีแหล่งอ้างอิง<br />จากไฟล์ข้อมูลที่เตรียมไว้</p>
            </div>
          </aside>

          <section className="conversation-panel" aria-label="แชทถามตอบสินค้า">
            <div className="conversation-header">
              <div className="conversation-identity">
                <div className="bot-avatar"><Bot size={18} strokeWidth={1.8} /></div>
                <div>
                  <h2>ผู้ช่วย Black Shark</h2>
                  <p className="conversation-status"><span className={`online-dot is-${catalogStatus}`} /> {catalogStatus === "ready" ? `พร้อมตอบเรื่อง${currentModeLabel}` : catalogStatus === "loading" ? "กำลังโหลดข้อมูลสินค้า" : "เชื่อมต่อฐานข้อมูลไม่ได้"}<span className="status-divider">·</span><span className="selected-product" title={selectedName}>{selectedName || "ยังไม่ได้เลือกสินค้า"}</span></p>
                </div>
              </div>
              <button className="new-chat-button" type="button" onClick={startNewChat} disabled={sending} aria-label="เริ่มแชทใหม่">
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
                    {message.kind === "handoff" && <div className="message-state handoff-state"><Headphones size={13} /> จำลองส่งต่อเจ้าหน้าที่</div>}
                    {message.kind === "need_model" && <div className="message-state question-state"><CircleHelp size={13} /> ขอข้อมูลเพิ่ม</div>}
                    {message.kind === "error" && <div className="message-state error-state"><CircleHelp size={13} /> เชื่อมต่อไม่สำเร็จ</div>}
                    <div className={`message-bubble ${message.role === "user" ? "user-bubble" : "assistant-bubble"} ${message.kind === "error" ? "error-bubble" : ""}`}>
                      <p>{message.text}</p>
                    </div>
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
                  onChange={(event) => { setDraft(event.target.value); setComposerError(""); }}
                  onKeyDown={handleComposerKey}
                  placeholder="พิมพ์คำถามเกี่ยวกับสินค้าหรือปัญหาที่พบ..."
                  aria-label="พิมพ์คำถาม"
                  rows={1}
                  maxLength={1000}
                  disabled={catalogLoading || catalogError || options.length === 0}
                />
                <button className="send-button" type="submit" disabled={sending || !draft.trim() || !selectedName || catalogLoading || catalogError} aria-label="ส่งคำถาม">
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
        </div>

        <footer className="page-footer">
          <span><span className={`footer-dot is-${catalogStatus}`} /> {catalogStatus === "loading" ? "กำลังเชื่อมต่อฐานข้อมูล" : catalogStatus === "offline" ? "ยังเชื่อมต่อฐานข้อมูลไม่ได้" : "ฐานข้อมูลพร้อมใช้งาน"}</span>
          <span>โหมดทดสอบ · {catalog?.counts.faq_entries ?? "—"} รายการคู่มือ</span>
        </footer>
      </main>
      {settingsOpen && <button className="settings-backdrop" type="button" aria-label="ปิดการตั้งค่าสินค้า" onClick={() => setSettingsOpen(false)} />}
    </div>
  );
}
