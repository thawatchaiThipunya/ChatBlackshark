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
const stages = ["intent", "vision", "answer"] as const;
const labels = { intent: "Intent / คัดสินค้า / FAQ", vision: "Image & Video", answer: "Answer" };

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
    if (!response.ok) throw new Error("บันทึกการตั้งค่าโมเดลไม่สำเร็จ");
    return response.json();
  }, []);

  const refresh = useCallback(async () => {
    setBusy(true); setError(""); setNotice("");
    try {
      const [keys, settings] = await Promise.all([call(""), callConfig("config")]);
      setRows(keys.keys); setConfig(settings); setLoaded(true);
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
