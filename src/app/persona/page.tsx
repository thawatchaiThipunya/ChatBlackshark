"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Check, RefreshCw, RotateCcw, Sparkles, UserRound } from "lucide-react";
import { ConsolePage } from "@/components/ConsolePage";

type PersonaLength = "short" | "medium" | "long";
type PersonaTone = "friendly" | "normal" | "very_polite";
type PersonaVoice = "male" | "female";
type PersonaConfig = { length: PersonaLength; tone: PersonaTone; voice: PersonaVoice; updated_at?: string | null };
type PersonaValues = Pick<PersonaConfig, "length" | "tone" | "voice">;

const lengthOptions: Array<{ value: PersonaLength; label: string; description: string }> = [
  { value: "short", label: "สั้น", description: "ตรงประเด็น อ่านจบเร็ว" },
  { value: "medium", label: "ปานกลาง", description: "มีบริบทพอดี ใช้งานทั่วไป" },
  { value: "long", label: "ยาว", description: "อธิบายรายละเอียดและทางเลือก" },
];

const toneOptions: Array<{ value: PersonaTone; label: string; description: string }> = [
  { value: "friendly", label: "เป็นกันเอง", description: "อบอุ่น เป็นธรรมชาติ" },
  { value: "normal", label: "ปกติ", description: "ชัดเจน สุภาพแบบทั่วไป" },
  { value: "very_polite", label: "สุภาพมาก", description: "ถ้อยคำสุภาพและให้เกียรติ" },
];

const voiceOptions: Array<{ value: PersonaVoice; label: string; description: string }> = [
  { value: "male", label: "ผู้ชาย", description: "ใช้สรรพนามและคำลงท้ายเสียงผู้ชาย" },
  { value: "female", label: "ผู้หญิง", description: "ใช้สรรพนามและคำลงท้ายเสียงผู้หญิง" },
];

async function requestJson<T>(method = "GET", body?: PersonaValues): Promise<T> {
  const response = await fetch("/api/llm/persona", {
    method,
    cache: "no-store",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (response.status === 401) {
    window.location.assign("/login?error=signed_out");
    throw new Error("เซสชันหมดอายุ กำลังกลับไปหน้าเข้าสู่ระบบ");
  }
  if (!response.ok) throw new Error("เชื่อมต่อการตั้งค่าบุคลิกการตอบไม่สำเร็จ");
  return await response.json() as T;
}

function toValues(config: PersonaConfig): PersonaValues {
  return { length: config.length, tone: config.tone, voice: config.voice };
}

function formatDate(value?: string | null) {
  if (!value) return "ยังไม่มีข้อมูลการบันทึก";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "ยังไม่มีข้อมูลการบันทึก";
  return new Intl.DateTimeFormat("th-TH", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function sampleReply(persona: PersonaValues) {
  const female = persona.voice === "female";
  const friendly = persona.tone === "friendly";
  const veryPolite = persona.tone === "very_polite";
  const statement = female ? "ค่ะ" : "ครับ";
  const question = female ? "คะ" : "ครับ";
  const product = friendly ? "รุ่นนี้รับประกันศูนย์ 2 ปีเลย" : "รุ่นนี้รับประกันศูนย์ 2 ปี";
  const intro = veryPolite ? "ขออนุญาตแจ้งว่า " : "";

  if (persona.length === "short") return `${intro}${product}${statement}`;
  if (persona.length === "medium") {
    if (friendly) return `${product}${statement} สนใจให้ช่วยเช็กเงื่อนไขเพิ่มเติมไหม${question}`;
    if (veryPolite) return `${intro}${product}${statement} หากต้องการข้อมูลเพิ่มเติม ยินดีช่วยตรวจสอบให้นะ${statement}`;
    return `${product}${statement} หากต้องการรายละเอียดเพิ่มเติม แจ้งได้เลย${statement}`;
  }

  if (friendly) return `${product}${statement} ประกันเริ่มนับจากวันที่ซื้อ และเป็นไปตามเงื่อนไขของศูนย์บริการนะ${statement} ถ้ามีเลขคำสั่งซื้อ ส่งมาให้ช่วยเช็กต่อได้เลย${statement}`;
  if (veryPolite) return `${intro}${product}${statement} ความคุ้มครองเป็นไปตามเงื่อนไขของศูนย์บริการและเริ่มนับจากวันที่ซื้อ หากมีเลขคำสั่งซื้อ ยินดีช่วยตรวจสอบรายละเอียดให้เพิ่มเติมนะ${statement}`;
  return `${product}${statement} ความคุ้มครองเป็นไปตามเงื่อนไขของศูนย์บริการและเริ่มนับจากวันที่ซื้อ หากมีเลขคำสั่งซื้อ สามารถแจ้งเพื่อให้ช่วยตรวจสอบรายละเอียดเพิ่มเติมได้${statement}`;
}

export default function PersonaPage() {
  const [persona, setPersona] = useState<PersonaConfig | null>(null);
  const [savedPersona, setSavedPersona] = useState<PersonaConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    setNotice("");
    try {
      const result = await requestJson<PersonaConfig>();
      setPersona(result);
      setSavedPersona(result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "โหลดการตั้งค่าบุคลิกไม่สำเร็จ");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const changed = !!persona && !!savedPersona && JSON.stringify(toValues(persona)) !== JSON.stringify(toValues(savedPersona));

  function update<K extends keyof PersonaValues>(key: K, value: PersonaValues[K]) {
    setPersona((current) => current ? { ...current, [key]: value } : current);
    setNotice("");
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!persona || !changed) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await requestJson<PersonaConfig>("POST", toValues(persona));
      setPersona(result);
      setSavedPersona(result);
      setNotice("บันทึกบุคลิกการตอบแล้ว");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "บันทึกบุคลิกการตอบไม่สำเร็จ");
    } finally {
      setBusy(false);
    }
  }

  return <ConsolePage icon={UserRound} title="บุคลิกการตอบ" subtitle="กำหนดความยาว น้ำเสียง และเสียงของผู้ช่วยสำหรับคำตอบใหม่"
    actions={<button className="header-action" type="button" onClick={() => void refresh()} disabled={loading || busy}><RefreshCw size={15} /> รีเฟรช</button>}>
    {error && (loading ? <p className="load-error" role="alert">{error}</p> : <div className="persona-inline-error" role="alert"><span>{error}</span><button type="button" onClick={() => void refresh()}>ลองอีกครั้ง</button></div>)}
    {notice && <p className="setting-feedback" role="status">{notice}</p>}

    {loading ? <section className="llm-panel persona-loading" aria-label="กำลังโหลดบุคลิก"><span className="persona-skeleton" /><span className="persona-skeleton" /><span className="persona-skeleton" /></section> : persona && <form onSubmit={(event) => void save(event)}>
      <div className="persona-layout">
        <section className="llm-panel persona-editor" aria-labelledby="persona-editor-title">
          <div className="panel-heading"><div><h2 id="persona-editor-title">รูปแบบการตอบ</h2><p>เลือกค่าที่เหมาะกับวิธีสื่อสารของแบรนด์</p></div><UserRound size={19} /></div>

          <fieldset className="persona-choice-group">
            <legend>ความยาวคำตอบ</legend>
            <div className="persona-choice-grid">
              {lengthOptions.map((option) => <label className="persona-choice" key={option.value}>
                <input type="radio" name="persona-length" value={option.value} checked={persona.length === option.value} onChange={() => update("length", option.value)} />
                <span><strong>{option.label}</strong><small>{option.description}</small></span>
              </label>)}
            </div>
          </fieldset>

          <fieldset className="persona-choice-group">
            <legend>น้ำเสียง</legend>
            <div className="persona-choice-grid">
              {toneOptions.map((option) => <label className="persona-choice" key={option.value}>
                <input type="radio" name="persona-tone" value={option.value} checked={persona.tone === option.value} onChange={() => update("tone", option.value)} />
                <span><strong>{option.label}</strong><small>{option.description}</small></span>
              </label>)}
            </div>
          </fieldset>

          <fieldset className="persona-choice-group">
            <legend>เสียงของผู้ช่วย</legend>
            <div className="persona-choice-grid is-voice">
              {voiceOptions.map((option) => <label className="persona-choice" key={option.value}>
                <input type="radio" name="persona-voice" value={option.value} checked={persona.voice === option.value} onChange={() => update("voice", option.value)} />
                <span><strong>{option.label}</strong><small>{option.description}</small></span>
              </label>)}
            </div>
          </fieldset>

          <div className="persona-save-row">
            <span className="persona-updated">บันทึกล่าสุด {formatDate(savedPersona?.updated_at)}</span>
            <div className="choice-row">
              {changed && <button className="setting-choice" type="button" disabled={busy} onClick={() => { setPersona(savedPersona); setNotice(""); setError(""); }}><RotateCcw size={14} /> คืนค่า</button>}
              <button className="llm-primary-action" type="submit" disabled={!changed || busy}><Check size={15} /> {busy ? "กำลังบันทึก…" : "บันทึกบุคลิก"}</button>
            </div>
          </div>
        </section>

        <aside className="llm-panel persona-preview" aria-labelledby="persona-preview-title" aria-live="polite">
          <div className="panel-heading"><div><h2 id="persona-preview-title">ตัวอย่างคำตอบ</h2><p>ปรับตามตัวเลือกทันที</p></div><Sparkles size={19} /></div>
          <p className="persona-preview-question">คำถามตัวอย่าง · “รุ่นนี้มีประกันกี่ปี?”</p>
          <div className="persona-preview-answer">{sampleReply(persona)}</div>
          <p className="persona-preview-caption">ตัวอย่างนี้ใช้ประกอบการตั้งค่า คำตอบจริงจะอ้างอิงข้อมูลสินค้าและเงื่อนไขในระบบ</p>
        </aside>
      </div>
    </form>}
  </ConsolePage>;
}
