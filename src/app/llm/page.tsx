"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { KeyRound, Plus, RefreshCw, Trash2 } from "lucide-react";
import { ConsolePage } from "@/components/ConsolePage";

type KeyRow = { id: number; label: string; last4: string; created_at: string };

export default function LlmKeysPage() {
  const [label, setLabel] = useState("");
  const [key, setKey] = useState("");
  const [rows, setRows] = useState<KeyRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

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

  const refresh = useCallback(async () => {
    setBusy(true); setError(""); setNotice("");
    try { setRows((await call("")).keys); setLoaded(true); }
    catch (reason) { setLoaded(false); setError(reason instanceof Error ? reason.message : "โหลดคีย์ไม่สำเร็จ"); }
    finally { setBusy(false); }
  }, [call]);

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

  return <ConsolePage icon={KeyRound} title="จัดการ Gemini keys" subtitle="เพิ่มและลบคีย์ที่ระบบใช้สร้างคำตอบ คีย์เต็มจะไม่แสดงหลังบันทึก"
    actions={<button className="header-action" type="button" onClick={() => void refresh()} disabled={busy}><RefreshCw size={15} /> รีเฟรช</button>}>
    {error && <p className="load-error" role="alert">{error}</p>}{notice && <p className="setting-feedback" role="status">{notice}</p>}
    <div className="llm-key-grid">
      <section className="llm-panel" aria-labelledby="key-list-title">
        <div className="panel-heading"><div><h2 id="key-list-title">คีย์ที่บันทึกไว้</h2><p>{loaded ? `${rows.length} คีย์` : "ตรวจสอบรายการคีย์"}</p></div><KeyRound size={18} /></div>
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
  </ConsolePage>;
}
