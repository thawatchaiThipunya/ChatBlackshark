"use client";

import { useCallback, useEffect, useState, type CSSProperties, type FormEvent } from "react";
import { Check, RefreshCw, RotateCcw, Sparkles, UserRound } from "lucide-react";
import { ConsolePage } from "@/components/ConsolePage";

type ReplyLength = "short" | "medium" | "long";
type Voice = "male" | "female";
type PersonaConfig = {
  bot_name: string;
  persona_text: string;
  friendliness: number;
  length: ReplyLength;
  detail_level: number;
  voice: Voice;
  updated_at?: string | null;
};
type PersonaValues = Pick<PersonaConfig, "bot_name" | "persona_text" | "friendliness" | "length" | "detail_level" | "voice">;

const LENGTH_STEPS: Array<{ value: ReplyLength; label: string }> = [
  { value: "short", label: "สั้น" },
  { value: "medium", label: "ปานกลาง" },
  { value: "long", label: "ละเอียด" },
];

function valuesOf(config: PersonaConfig): PersonaValues {
  return {
    bot_name: config.bot_name,
    persona_text: config.persona_text,
    friendliness: config.friendliness,
    length: config.length,
    detail_level: config.detail_level,
    voice: config.voice,
  };
}

function lengthPosition(length: ReplyLength) {
  return length === "short" ? 0 : length === "long" ? 100 : 50;
}

function lengthFromPosition(position: number): ReplyLength {
  return position < 34 ? "short" : position < 67 ? "medium" : "long";
}

function toneDescription(friendliness: number) {
  if (friendliness < 25) return "เป็นทางการ สุภาพ และเว้นระยะตามกาลเทศะ";
  if (friendliness < 50) return "สุภาพ เป็นมิตร และไม่สนิทสนมเกินไป";
  if (friendliness < 75) return "เป็นกันเองและอบอุ่น คุยเหมือนพนักงานที่พร้อมช่วยเหลือ";
  return "เป็นกันเองมาก อบอุ่น เห็นใจลูกค้า ใช้คำพูดเป็นธรรมชาติ ไม่ฝืนเล่นมุกหรือใช้อีโมจิเกินจำเป็น";
}

function detailDescription(detailLevel: number) {
  if (detailLevel < 34) return "กระชับที่สุด ตอบตรงคำถามใน 1–3 ประโยค แต่ต้องครอบคลุมทุก need";
  if (detailLevel < 67) return "ให้รายละเอียดพอดี ตอบครบทุก need พร้อมเหตุผลหรือจุดเด่นที่เกี่ยวข้อง";
  return "อธิบายละเอียดเป็นลำดับ เพิ่มบริบทที่ช่วยตัดสินใจ โดยไม่พูดซ้ำหรือเติมข้อมูลนอกหลักฐาน";
}

function lengthDescription(length: ReplyLength) {
  if (length === "short") return "ตอบกระชับ 1–3 ประโยค ตอบทุกประเด็นที่ลูกค้าถาม หากเป็นขั้นตอนหรือเทียบหลายรุ่นให้ใช้พื้นที่เท่าที่จำเป็น";
  if (length === "long") return "ตอบละเอียดขึ้นพร้อมเหตุผลและรายละเอียดที่เกี่ยวข้อง จัดเป็นย่อหน้าหรือรายการเมื่อช่วยให้อ่านง่าย";
  return "ตอบความยาวปานกลาง อธิบายเหตุผลหรือจุดเด่นที่เกี่ยวข้องโดยไม่ยืดเยื้อ";
}

function promptPreview(persona: PersonaValues) {
  const voice = persona.voice === "female"
    ? "ใช้รูปแบบคำลงท้ายแบบหญิง เช่น ค่ะ/คะ ให้ถูกบริบท ไม่ต้องลงท้ายทุกประโยค"
    : "ใช้รูปแบบคำลงท้ายแบบชาย เช่น ครับ อย่างเป็นธรรมชาติ ไม่ต้องลงท้ายทุกประโยค";

  return [
    `ชื่อที่ใช้แทนตัวเองคือ ${persona.bot_name || "ผู้ช่วยร้าน"}`,
    `บุคลิกที่ผู้ดูแลร้านกำหนด: ${persona.persona_text || "ไม่ได้กำหนดข้อความเพิ่มเติม"}`,
    `ระดับความเป็นกันเอง ${persona.friendliness}/100: ${toneDescription(persona.friendliness)}`,
    lengthDescription(persona.length),
    `ระดับรายละเอียด ${persona.detail_level}/100: ${detailDescription(persona.detail_level)}`,
    voice,
    "หากลูกค้าขอรูปแบบหรือความยาวเป็นพิเศษ ให้ทำตาม; persona กำหนดเพียงสไตล์ ห้ามเปลี่ยนข้อเท็จจริงหรือเติมข้อมูลนอก SOURCE",
  ].join("; ");
}

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
  if (!response.ok) throw new Error("โหลดหรือบันทึก Persona ไม่สำเร็จ");
  return await response.json() as T;
}

function formatDate(value?: string | null) {
  if (!value) return "ยังไม่มีข้อมูลการบันทึก";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "ยังไม่มีข้อมูลการบันทึก";
  return new Intl.DateTimeFormat("th-TH", { dateStyle: "medium", timeStyle: "short" }).format(date);
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
      setError(reason instanceof Error ? reason.message : "โหลดการตั้งค่า Persona ไม่สำเร็จ");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const dirty = !!persona && !!savedPersona && JSON.stringify(valuesOf(persona)) !== JSON.stringify(valuesOf(savedPersona));
  const prompt = persona ? promptPreview(valuesOf(persona)) : "";

  function update<K extends keyof PersonaValues>(key: K, value: PersonaValues[K]) {
    setPersona((current) => current ? { ...current, [key]: value } : current);
    setNotice("");
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!persona || !dirty) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await requestJson<PersonaConfig>("POST", valuesOf(persona));
      setPersona(result);
      setSavedPersona(result);
      setNotice("บันทึก Persona แล้ว คำตอบใหม่จะใช้ค่าชุดนี้");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "บันทึก Persona ไม่สำเร็จ");
    } finally {
      setBusy(false);
    }
  }

  return <ConsolePage className="persona-page" icon={UserRound} title="ตั้งค่า Persona" subtitle="กำหนดชื่อผู้ช่วย บุคลิก และสไตล์คำตอบที่ใช้ใน system prompt">
    {error && (loading ? <p className="load-error" role="alert">{error}</p> : <div className="persona-inline-error" role="alert"><span>{error}</span><button type="button" onClick={() => void refresh()}>ลองอีกครั้ง</button></div>)}
    {notice && <p className="setting-feedback" role="status">{notice}</p>}

    {loading ? <section className="llm-panel persona-loading" aria-label="กำลังโหลด Persona"><span className="persona-skeleton" /><span className="persona-skeleton" /><span className="persona-skeleton" /></section> : persona && <form className="persona-layout" onSubmit={(event) => void save(event)}>
      <section className="llm-panel persona-editor" aria-labelledby="persona-editor-title">
        <div className="panel-heading">
          <div><h2 id="persona-editor-title">ตั้งค่า Persona</h2><p>กำหนดข้อความใน system prompt ทุกครั้งที่บอทตอบ</p></div>
          <button className="persona-refresh" type="button" onClick={() => void refresh()} disabled={loading || busy} aria-label="รีเฟรช Persona" title="รีเฟรช Persona"><RefreshCw size={16} /></button>
        </div>

        <div className="persona-field">
          <label htmlFor="persona-name">ชื่อบอท <small>ชื่อที่ใช้แทนตัว เช่น “น้อง Koop”</small></label>
          <input id="persona-name" value={persona.bot_name} onChange={(event) => update("bot_name", event.target.value)} maxLength={80} required />
        </div>

        <div className="persona-field">
          <label htmlFor="persona-text">บุคลิก / คำแทนตัว / โทนการพูด</label>
          <textarea id="persona-text" value={persona.persona_text} onChange={(event) => update("persona_text", event.target.value)} maxLength={2000} aria-describedby="persona-text-count" />
          <span id="persona-text-count" className="persona-count">{persona.persona_text.length}/2000</span>
        </div>

        <fieldset className="persona-slider-group">
          <legend>ความเป็นกันเอง <output htmlFor="persona-friendliness">{persona.friendliness}/100</output></legend>
          <input className="persona-range" id="persona-friendliness" type="range" min="0" max="100" step="1" value={persona.friendliness}
            style={{ "--range-progress": `${persona.friendliness}%` } as CSSProperties}
            onChange={(event) => update("friendliness", Number(event.target.value))} />
          <div className="persona-range-labels"><span>ทางการ</span><span className={persona.friendliness >= 25 && persona.friendliness < 75 ? "is-current" : ""}>สุภาพทั่วไป</span><span className={persona.friendliness >= 75 ? "is-current" : ""}>เป็นกันเองมาก</span></div>
          <p>{toneDescription(persona.friendliness)}</p>
        </fieldset>

        <fieldset className="persona-slider-group">
          <legend>ความยาวคำตอบ</legend>
          <input className="persona-range" id="persona-length" type="range" min="0" max="100" step="1" value={lengthPosition(persona.length)}
            style={{ "--range-progress": `${lengthPosition(persona.length)}%` } as CSSProperties}
            onChange={(event) => update("length", lengthFromPosition(Number(event.target.value)))} aria-label="ความยาวคำตอบ" />
          <div className="persona-range-labels">{LENGTH_STEPS.map((step) => <span key={step.value} className={persona.length === step.value ? "is-current" : ""}>{step.label}</span>)}</div>
          <p>{lengthDescription(persona.length)}</p>
        </fieldset>

        <fieldset className="persona-slider-group">
          <legend>ความกระชับ <output htmlFor="persona-detail">{persona.detail_level}/100</output></legend>
          <input className="persona-range" id="persona-detail" type="range" min="0" max="100" step="1" value={persona.detail_level}
            style={{ "--range-progress": `${persona.detail_level}%` } as CSSProperties}
            onChange={(event) => update("detail_level", Number(event.target.value))} />
          <div className="persona-range-labels"><span className={persona.detail_level < 34 ? "is-current" : ""}>ตรงประเด็น</span><span className={persona.detail_level >= 34 && persona.detail_level < 67 ? "is-current" : ""}>สมดุล</span><span className={persona.detail_level >= 67 ? "is-current" : ""}>อธิบายครบ</span></div>
          <p>{detailDescription(persona.detail_level)}</p>
        </fieldset>

        <fieldset className="persona-slider-group persona-voice-group">
          <legend>รูปแบบคำลงท้าย</legend>
          <div className="persona-voice-options" role="group" aria-label="เสียงของผู้ช่วย">
            <button type="button" className={persona.voice === "male" ? "is-selected" : ""} aria-pressed={persona.voice === "male"} onClick={() => update("voice", "male")}>ชาย <small>ครับ</small></button>
            <button type="button" className={persona.voice === "female" ? "is-selected" : ""} aria-pressed={persona.voice === "female"} onClick={() => update("voice", "female")}>หญิง <small>ค่ะ / คะ</small></button>
          </div>
        </fieldset>

        <div className="persona-save-row">
          <span className="persona-updated">บันทึกล่าสุด {formatDate(savedPersona?.updated_at)}</span>
          <div className="choice-row">
            {dirty && <button className="setting-choice" type="button" disabled={busy} onClick={() => { setPersona(savedPersona); setError(""); setNotice(""); }}><RotateCcw size={14} /> คืนค่า</button>}
            <button className="llm-primary-action" type="submit" disabled={!dirty || busy || !persona.bot_name.trim()}><Check size={15} /> {busy ? "กำลังบันทึก…" : "บันทึก Persona"}</button>
          </div>
        </div>
      </section>

      <aside className="llm-panel persona-preview" aria-labelledby="persona-preview-title" aria-live="polite">
        <div className="panel-heading"><div><h2 id="persona-preview-title">System Prompt ที่บอทเห็น</h2><p>ตัวอย่างจริง — ส่วนที่ไฮไลต์เปลี่ยนตามค่าด้านซ้าย</p></div><Sparkles size={19} /></div>
        <pre className="persona-prompt-preview"><span>PERSONA: </span><mark>{prompt}</mark>{"\n\n"}
          <span>INTENT · TOPIC NEEDS · PRODUCTS · HISTORY · CUSTOMER · SOURCE</span>{"\n"}
          <span>ถูกเติมจากข้อความและบริบทในแต่ละคำขอ</span>
        </pre>
        <p className="persona-preview-caption">แสดงบล็อก PERSONA รูปแบบเดียวกับที่ backend ใช้จริง ข้อมูลคำถาม สินค้า และ SOURCE จะถูกเติมตามแต่ละคำขอ</p>
      </aside>
    </form>}
  </ConsolePage>;
}
