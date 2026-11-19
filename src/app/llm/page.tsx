"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { KeyRound, Plus, RefreshCw, Trash2 } from "lucide-react";

type KeyRow = { id: number; label: string; last4: string; created_at: string };
type Config = {
  provider: "gemini" | "openrouter";
  gemini_intent_model: string; gemini_vision_model: string; gemini_answer_model: string;
  openrouter_intent_model: string; openrouter_vision_model: string; openrouter_answer_model: string;
  openrouter_key_last4: string | null;
};
type Persona = {
  bot_name: string; persona_text: string; friendliness: number;
  length: "short" | "medium" | "long"; detail_level: number; voice: "male" | "female";
};
const stages = ["intent", "vision", "answer"] as const;
const labels = { intent: "Intent / คัดสินค้า / FAQ", vision: "Image & Video", answer: "Answer" };

function cleanPersona(value: Persona): Persona {
  return { bot_name: value.bot_name, persona_text: value.persona_text, friendliness: value.friendliness,
    length: value.length, detail_level: value.detail_level, voice: value.voice };
}

function personaPromptPreview(persona: Persona): string {
  const tone = persona.friendliness < 25 ? "เป็นทางการ สุภาพ และเว้นระยะตามกาลเทศะ"
    : persona.friendliness < 50 ? "สุภาพ เป็นมิตร และไม่สนิทสนมเกินไป"
      : persona.friendliness < 75 ? "เป็นกันเองและอบอุ่น คุยเหมือนพนักงานที่พร้อมช่วยเหลือ"
        : "เป็นกันเองมาก อบอุ่น เห็นใจลูกค้า ใช้คำพูดเป็นธรรมชาติ ไม่ฝืนเล่นมุกหรือใช้อีโมจิเกินจำเป็น";
  const detail = persona.detail_level < 34 ? "กระชับที่สุด ตอบตรงคำถามใน 1–3 ประโยค แต่ครอบคลุมทุกประเด็น"
    : persona.detail_level < 67 ? "ให้รายละเอียดพอดี ตอบครบทุกประเด็น พร้อมเหตุผลหรือจุดเด่นที่เกี่ยวข้อง"
      : "อธิบายละเอียดเป็นลำดับ เพิ่มบริบทที่ช่วยตัดสินใจ โดยไม่พูดซ้ำ";
  const length = persona.length === "short" ? "สั้น กระชับ"
    : persona.length === "long" ? "ยาวและอธิบายละเอียด" : "ความยาวปานกลาง";
  const voice = persona.voice === "female" ? "ใช้คำลงท้ายแบบหญิง เช่น ค่ะ/คะ ให้ถูกบริบท"
    : "ใช้คำลงท้ายแบบชาย เช่น ครับ อย่างเป็นธรรมชาติ";
  return [
    `คุณคือผู้ช่วยร้าน Black Shark ชื่อ ${persona.bot_name || "ผู้ช่วยร้าน"} ตอบลูกค้าเป็นภาษาไทย`,
    `บุคลิกและกติกาการพูดที่ผู้ดูแลร้านกำหนด: ${persona.persona_text || ""}`,
    `ความเป็นกันเอง (${persona.friendliness}/100): ${tone}`,
    `ความยาวคำตอบ: ${length}`,
    `ความกระชับและรายละเอียด (${persona.detail_level}/100): ${detail}`,
    `รูปแบบคำลงท้าย: ${voice}`,
    "กฎบังคับ:",
    "- ตอบจากข้อมูลสินค้าและ FAQ ของร้านที่แนบมาเท่านั้น ห้ามแต่งสเปค ราคา หรือเงื่อนไข",
    "- ถ้าข้อมูลไม่พอหรือขัดกัน ให้บอกอย่างสุภาพว่าไม่พบข้อมูลหรือยังยืนยันไม่ได้",
    "- ทำตามรูปแบบที่ลูกค้าขอ และตอบให้ครบทุกประเด็น",
    "- ใช้ persona เพื่อกำหนดสไตล์เท่านั้น ห้ามเปลี่ยนข้อเท็จจริงในแหล่งข้อมูล",
  ].join("\n\n");
}

export default function LlmKeysPage() {
  const [label, setLabel] = useState("");
  const [key, setKey] = useState("");
  const [rows, setRows] = useState<KeyRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [config, setConfig] = useState<Config | null>(null);
  const [openrouterKey, setOpenrouterKey] = useState("");
  const [persona, setPersona] = useState<Persona | null>(null);
  const [savedPersona, setSavedPersona] = useState<Persona | null>(null);

  const call = useCallback(async (path: string, method = "GET", body?: object) => {
    const response = await fetch(`/api/llm/keys${path}`, {
      method, cache: "no-store",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (response.status === 401) {
      window.location.assign("/login?error=signed_out");
      throw new Error("เซสชันหมดอายุ กำลังกลับไปหน้าเข้าสู่ระบบ");
    }
    if (!response.ok) throw new Error("เชื่อมต่อที่เก็บคีย์ไม่ได้");
    return response.json();
  }, []);

  const callConfig = useCallback(async (path: string, method = "GET", body?: object) => {
    const response = await fetch(`/api/llm/${path}`, { method, cache: "no-store",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined });
    if (response.status === 401) { window.location.assign("/login?error=signed_out"); throw new Error("เซสชันหมดอายุ"); }
    if (!response.ok) throw new Error("โหลดหรือบันทึกการตั้งค่าบอทไม่สำเร็จ");
    return response.json();
  }, []);

  const refresh = useCallback(async () => {
    setBusy(true); setError(""); setNotice("");
    try {
      const [keys, settings, personaSettings] = await Promise.all([call(""), callConfig("config"), callConfig("persona")]);
      const clean = cleanPersona(personaSettings);
      setRows(keys.keys); setConfig(settings); setPersona(clean); setSavedPersona(clean); setLoaded(true);
    }
    catch (reason) { setLoaded(false); setError(reason instanceof Error ? reason.message : "โหลดคีย์ไม่สำเร็จ"); }
    finally { setBusy(false); }
  }, [call, callConfig]);

  async function saveConfig(event: FormEvent) {
    event.preventDefault(); if (!config) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const { openrouter_key_last4: _last4, ...fields } = config;
      const saved = await callConfig("config", "POST", fields);
      setConfig(saved); setNotice("บันทึก provider และโมเดลแล้ว");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "บันทึกไม่สำเร็จ"); }
    finally { setBusy(false); }
  }

  async function saveOpenrouterKey(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    try {
      const saved = await callConfig("openrouter-key", "POST", { key: openrouterKey });
      setConfig(saved); setOpenrouterKey(""); setNotice("บันทึก OpenRouter key แล้ว");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "บันทึกคีย์ไม่สำเร็จ"); }
    finally { setBusy(false); }
  }

  async function savePersona(event: FormEvent) {
    event.preventDefault(); if (!persona) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const saved = cleanPersona(await callConfig("persona", "POST", persona));
      setPersona(saved); setSavedPersona(saved); setNotice("บันทึกบุคลิกบอทแล้ว คำตอบใหม่จะใช้ค่านี้");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "บันทึกบุคลิกไม่สำเร็จ"); }
    finally { setBusy(false); }
  }

  useEffect(() => { void refresh(); }, [refresh]);

  async function add(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    try {
      await call("", "POST", { label, key });
      setKey(""); setLabel("");
      setRows((await call("")).keys); setLoaded(true); setNotice("เพิ่มคีย์เรียบร้อยแล้ว");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "เพิ่มคีย์ไม่สำเร็จ"); }
    finally { setBusy(false); }
  }

  async function remove(id: number) {
    setBusy(true); setError(""); setNotice("");
    try { await call(`/${id}`, "DELETE"); setRows((await call("")).keys); setNotice("ลบคีย์เรียบร้อยแล้ว"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "ลบคีย์ไม่สำเร็จ"); }
    finally { setBusy(false); }
  }

  return <main className="llm-console"><div className="llm-console-inner">
    <header className="admin-page-header"><div><h1>Provider และโมเดล AI</h1><p>เลือกโมเดลแยกตามขั้นตอน และสลับระหว่าง Gemini กับ OpenRouter</p></div>
      <button className="header-action" type="button" onClick={() => void refresh()} disabled={busy}><RefreshCw size={15} /> รีเฟรช</button>
    </header>
    {error && <p className="load-error" role="alert">{error}</p>}{notice && <p className="setting-feedback" role="status">{notice}</p>}
    {persona && <section className="persona-layout" aria-label="ตั้งค่าและตัวอย่าง Persona">
      <div className="llm-panel persona-editor">
        <h2 id="persona-title">ตั้งค่า Persona</h2>
        <p className="llm-note">บอทจะเอาค่านี้ไปแทรกใน system prompt ทุกครั้งที่ตอบ</p>
        <form className="llm-form" onSubmit={(event) => void savePersona(event)}>
          <label htmlFor="persona-name">ชื่อบอท (ชื่อที่ใช้แทนตัว เช่น “น้อง Koop”)</label>
          <input id="persona-name" value={persona.bot_name} onChange={(event) => setPersona({ ...persona, bot_name: event.target.value })} maxLength={80} required />
          <label htmlFor="persona-description">บุคลิก / คำแทนตัว / โทนการพูด</label>
          <textarea id="persona-description" className="persona-description" value={persona.persona_text}
            onChange={(event) => setPersona({ ...persona, persona_text: event.target.value })} maxLength={2000}
            aria-describedby="persona-description-count" />
          <div id="persona-description-count" className="persona-count">{persona.persona_text.length}/2000</div>

          <label htmlFor="persona-friendliness">ความเป็นกันเอง <output>{persona.friendliness}/100</output></label>
          <input className="persona-range" id="persona-friendliness" type="range" min="0" max="100" step="1"
            value={persona.friendliness} onChange={(event) => setPersona({ ...persona, friendliness: Number(event.target.value) })} />
          <div className="persona-range-labels"><span>ทางการ</span><span>สุภาพทั่วไป</span><span>เป็นกันเองมาก</span></div>
          <p className="persona-range-help">ปรับโทนให้สุภาพหรืออบอุ่นขึ้น โดยยังรักษาความเหมาะสมกับลูกค้า</p>

          <label>ความยาวคำตอบ</label>
          <div className="persona-length-options" role="group" aria-label="ความยาวคำตอบ">
            {([ ["short", "สั้น"], ["medium", "ปานกลาง"], ["long", "ละเอียด"] ] as const).map(([value, title]) =>
              <button key={value} type="button" aria-pressed={persona.length === value}
                className={persona.length === value ? "is-selected" : ""}
                onClick={() => setPersona({ ...persona, length: value })}>{title}</button>)}
          </div>
          <p className="persona-range-help">{persona.length === "short" ? "ตอบสั้นที่สุดเท่าที่จะครอบคลุมคำถามได้" : persona.length === "long" ? "อธิบายรายละเอียดและบริบทที่ช่วยตัดสินใจ" : "ตอบครบในความยาวพอดี ไม่ยืดเยื้อ"}</p>

          <label htmlFor="persona-detail">ความกระชับ <output>{persona.detail_level}/100</output></label>
          <input className="persona-range" id="persona-detail" type="range" min="0" max="100" step="1"
            value={persona.detail_level} onChange={(event) => setPersona({ ...persona, detail_level: Number(event.target.value) })} />
          <div className="persona-range-labels"><span>ตรงประเด็น</span><strong>สมดุล</strong><span>อธิบายครบ</span></div>
          <p className="persona-range-help">เลือกว่าต้องการให้สรุปสั้นหรือใส่รายละเอียดช่วยตัดสินใจมากขึ้น</p>

          <label htmlFor="persona-voice">รูปแบบคำลงท้าย</label>
          <select id="persona-voice" value={persona.voice} onChange={(event) => setPersona({ ...persona, voice: event.target.value as Persona["voice"] })}>
            <option value="male">ชาย (ครับ)</option><option value="female">หญิง (ค่ะ/คะ)</option>
          </select>
          <button type="submit" disabled={busy || JSON.stringify(persona) === JSON.stringify(savedPersona)}>บันทึก Persona</button>
        </form>
      </div>
      <aside className="llm-panel persona-preview" aria-labelledby="persona-preview-title">
        <h2 id="persona-preview-title">System Prompt ที่บอทเห็น</h2>
        <p className="llm-note">ตัวอย่างจริง — ส่วนที่ไฮไลต์เปลี่ยนตามค่าด้านซ้าย</p>
        <pre><span>คุณคือผู้ช่วยร้าน Black Shark ตอบคำถามลูกค้าเป็นภาษาไทย<br />โดยอ้างอิงจากข้อมูลสินค้าและ FAQ ที่แนบมาใน context เท่านั้น</span>{"\n\n"}
          <mark>{personaPromptPreview(persona).split("\n\n").slice(0, 6).join("\n")}</mark>{"\n\n"}
          <span>กฎบังคับ:<br />{personaPromptPreview(persona).split("\n\n").slice(6).join("\n")}</span>
        </pre>
      </aside>
    </section>}
    {config && <section className="llm-panel" aria-labelledby="routing-title" style={{ marginBottom: 16 }}>
      <h2 id="routing-title">การทำงานของบอท</h2>
      <form className="llm-form" onSubmit={(event) => void saveConfig(event)}>
        <label htmlFor="provider">Provider ที่ใช้งาน</label>
        <select id="provider" value={config.provider} onChange={(event) => setConfig({ ...config, provider: event.target.value as Config["provider"] })}>
          <option value="gemini">Gemini — หมุนเวียนคีย์</option><option value="openrouter">OpenRouter — คีย์เดียว</option>
        </select>
        {(["gemini", "openrouter"] as const).map((provider) => <fieldset key={provider} className="llm-model-group">
          <legend>{provider === "gemini" ? "โมเดล Gemini" : "โมเดล OpenRouter"}</legend>
          {stages.map((stage) => {
            const field = `${provider}_${stage}_model` as keyof Config;
            return <div key={field}><label htmlFor={field}>{labels[stage]}</label>
              <input id={field} value={config[field] || ""} required maxLength={120}
                onChange={(event) => setConfig({ ...config, [field]: event.target.value })}
                placeholder={provider === "gemini" ? "gemini-3.5-flash-lite" : "google/gemini-2.5-flash"} /></div>;
          })}
        </fieldset>)}
        <button type="submit" disabled={busy || (config.provider === "openrouter" && !config.openrouter_key_last4)}>บันทึกการตั้งค่า</button>
        {config.provider === "openrouter" && !config.openrouter_key_last4 && <p className="llm-note">เพิ่ม OpenRouter key ก่อนสลับใช้งาน</p>}
      </form>
    </section>}
    <section className="llm-panel" aria-labelledby="or-key-title" style={{ marginBottom: 16 }}>
      <h2 id="or-key-title">OpenRouter key</h2>
      <p className="llm-note">{config?.openrouter_key_last4 ? `บันทึกแล้ว •••• ${config.openrouter_key_last4}` : "ยังไม่มีคีย์"}</p>
      <form className="llm-form" onSubmit={(event) => void saveOpenrouterKey(event)}>
        <label htmlFor="openrouter-key">คีย์ใหม่</label>
        <input id="openrouter-key" type="password" value={openrouterKey} onChange={(event) => setOpenrouterKey(event.target.value)} minLength={10} required autoComplete="new-password" placeholder="วาง OpenRouter API key" />
        <button type="submit" disabled={busy || openrouterKey.trim().length < 10}>บันทึก OpenRouter key</button>
      </form>
    </section>
    <div className="llm-key-grid">
      <section className="llm-panel" aria-labelledby="key-list-title">
        <div className="panel-heading"><div><h2 id="key-list-title">Gemini keys</h2><p>{loaded ? `${rows.length} คีย์` : "ตรวจสอบรายการคีย์"}</p></div><KeyRound size={18} /></div>
        {busy && !loaded ? <p className="empty-copy">กำลังโหลดรายการคีย์…</p> : rows.length ? <ul className="llm-key-list">{rows.map((row) => <li key={row.id}>
          <div><strong>{row.label}</strong><small>•••• {row.last4}</small></div>
          <button type="button" disabled={busy} onClick={() => void remove(row.id)} aria-label={`ลบ ${row.label}`}><Trash2 size={14} /> ลบ</button>
        </li>)}</ul> : <p className="empty-copy">ยังไม่มีคีย์ เพิ่ม Gemini API key เพื่อเริ่มตอบคำถาม</p>}
      </section>
      <section className="llm-panel" aria-labelledby="add-key-title">
        <h2 id="add-key-title">เพิ่มคีย์ใหม่</h2>
        <form className="llm-form" onSubmit={(event) => void add(event)}>
          <label htmlFor="key-label">ชื่อเรียก</label>
          <input id="key-label" value={label} onChange={(event) => setLabel(event.target.value)} maxLength={80} required placeholder="เช่น Gemini คีย์หลัก" />
          <label htmlFor="gemini-key">Gemini API key</label>
          <input id="gemini-key" type="password" value={key} onChange={(event) => setKey(event.target.value)} minLength={10} required autoComplete="new-password" placeholder="วาง API key ที่นี่" />
          <button type="submit" disabled={busy || !label.trim() || !key.trim()}><Plus size={16} /> บันทึกคีย์</button>
        </form>
        <p className="llm-note">จัดเก็บด้วยการเข้ารหัส และแสดงเพียง 4 ตัวท้ายในรายการ</p>
      </section>
    </div>
    <Link className="llm-back-link" href="/dashboard">กลับไปหน้า Dashboard</Link>
  </div></main>;
}
