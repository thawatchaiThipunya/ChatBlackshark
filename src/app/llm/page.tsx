"use client";

import { useEffect, useState, type FormEvent } from "react";

type KeyRow = { id: number; label: string; last4: string; created_at: string };

export default function LlmKeysPage() {
  const [password, setPassword] = useState("");
  const [label, setLabel] = useState("");
  const [key, setKey] = useState("");
  const [rows, setRows] = useState<KeyRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [authorized, setAuthorized] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");

  async function call(path: string, method = "GET", body?: object) {
    const response = await fetch(`/api/llm/keys${path}`, {
      method, cache: "no-store",
      headers: { "X-Admin-Password": password, ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!response.ok) throw new Error(response.status === 401 ? "admin_unauthorized" : "เชื่อมต่อที่เก็บคีย์ไม่ได้");
    return response.json();
  }

  async function refresh() {
    setBusy(true); setError("");
    try { setRows((await call("")).keys); setLoaded(true); setAuthorized(true); }
    catch (err) {
      setAuthorized(false); setLoaded(false);
      const message = err instanceof Error ? err.message : "เกิดข้อผิดพลาด";
      setError(message === "admin_unauthorized" ? (password ? "รหัสผู้ดูแลไม่ถูกต้อง" : "") : message);
    }
    finally { setBusy(false); }
  }

  useEffect(() => { void refresh(); }, []);

  async function add(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      await call("", "POST", { label, key });
      setKey(""); setLabel("");
      setRows((await call("")).keys); setLoaded(true);
    } catch (err) { setError(err instanceof Error ? err.message : "เพิ่มคีย์ไม่สำเร็จ"); }
    finally { setBusy(false); }
  }

  async function remove(id: number) {
    setBusy(true); setError("");
    try { await call(`/${id}`, "DELETE"); setRows((await call("")).keys); }
    catch (err) { setError(err instanceof Error ? err.message : "ลบคีย์ไม่สำเร็จ"); }
    finally { setBusy(false); }
  }

  return <main className="llm-page">
    <header><a href="/">← กลับไปหน้าแชท</a><span>BLACK SHARK · BOT SETTINGS</span></header>
    <section className="llm-card">
      <div className="llm-kicker">GEMINI API KEYS</div>
      <h1>จัดการคีย์ของบอท</h1>
      <p>เพิ่มได้หลายคีย์ บอทจะวนใช้ทุกคีย์ในฐานข้อมูลและลองคีย์ถัดไปเมื่อคีย์หนึ่งใช้ไม่ได้</p>
      {!authorized && <>
        {busy && <p className="llm-empty">กำลังตรวจสิทธิ์...</p>}
        {!busy && <>
          <label htmlFor="admin-password">รหัสผู้ดูแล (ใช้เฉพาะหน้า Vercel)</label>
          <div className="llm-inline">
            <input id="admin-password" type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="off" placeholder="กรอกรหัสผู้ดูแล" />
            <button type="button" onClick={() => void refresh()} disabled={!password || busy}>ดูคีย์</button>
          </div>
        </>}
      </>}
      {error && <p className="llm-error" role="alert">{error}</p>}
      {authorized && <p className="llm-note">ตรวจสอบสิทธิ์แล้ว · คีย์เต็มไม่ถูกส่งกลับมาแสดง</p>}
      {loaded && <>
        <div className="llm-list-title"><h2>คีย์ที่บันทึกไว้</h2><span>{rows.length} คีย์</span></div>
        {rows.length ? <ul className="llm-list">{rows.map(row => <li key={row.id}>
          <div><strong>{row.label}</strong><small>•••• {row.last4}</small></div>
          <button type="button" disabled={busy} onClick={() => void remove(row.id)}>ลบ</button>
        </li>)}</ul> : <p className="llm-empty">ยังไม่มีคีย์ เพิ่ม Gemini API key ด้านล่างเพื่อเริ่มตอบคำถาม</p>}
        <form onSubmit={e => void add(e)}>
          <h2>เพิ่มคีย์</h2>
          <label htmlFor="key-label">ชื่อเรียก</label>
          <input id="key-label" value={label} onChange={e => setLabel(e.target.value)} maxLength={80} required placeholder="เช่น Gemini คีย์หลัก" />
          <label htmlFor="gemini-key">Gemini API key</label>
          <input id="gemini-key" type="password" value={key} onChange={e => setKey(e.target.value)} minLength={10} required autoComplete="off" placeholder="วาง API key ที่นี่" />
          <button type="submit" disabled={busy || !label.trim() || !key.trim()}>บันทึกคีย์</button>
        </form>
      </>}
      <p className="llm-note">หน้าเว็บจะแสดงเฉพาะท้ายคีย์ 4 ตัว คีย์เต็มถูกเข้ารหัสก่อนเก็บใน Supabase</p>
    </section>
  </main>;
}
