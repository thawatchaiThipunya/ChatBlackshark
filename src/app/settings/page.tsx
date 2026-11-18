"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, LoaderCircle, Palette, Settings2, Type } from "lucide-react";
import { ConsolePage } from "@/components/ConsolePage";

type Preferences = { bubble_color: string; chat_text_size: "small" | "medium" | "large"; theme_preference: "light" | "dark" };
const defaults: Preferences = { bubble_color: "#8b1e28", chat_text_size: "medium", theme_preference: "light" };
const colors = ["#8b1e28", "#0b2340", "#2255a4", "#087e77", "#6643a5"];
const sizes: Array<{ value: Preferences["chat_text_size"]; label: string }> = [
  { value: "small", label: "เล็ก · 14px" }, { value: "medium", label: "ปกติ · 16px" }, { value: "large", label: "ใหญ่ · 18px" },
];

function applyPreferences(preferences: Preferences) {
  const root = document.documentElement;
  root.style.setProperty("--user-bubble", preferences.bubble_color);
  root.dataset.chatTextSize = preferences.chat_text_size;
  root.dataset.theme = preferences.theme_preference;
}

export default function SettingsPage() {
  const [preferences, setPreferences] = useState<Preferences>(defaults);
  const [saved, setSaved] = useState<Preferences>(defaults);
  const [loading, setLoading] = useState(true);
  const [ready, setReady] = useState(false);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [isError, setIsError] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setReady(false); setFeedback("");
    try {
      const response = await fetch("/api/admin/preferences", { cache: "no-store" });
      if (!response.ok) throw new Error("โหลดการตั้งค่าไม่สำเร็จ");
      const result = { ...defaults, ...await response.json() as Preferences };
      setPreferences(result); setSaved(result); applyPreferences(result); setReady(true);
    } catch (reason) { setFeedback(reason instanceof Error ? reason.message : "โหลดการตั้งค่าไม่สำเร็จ"); setIsError(true); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  function update(next: Preferences) {
    setPreferences(next); setFeedback(""); setIsError(false); applyPreferences(next);
  }

  async function save() {
    setSaving(true); setFeedback(""); setIsError(false);
    try {
      const response = await fetch("/api/admin/preferences", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(preferences) });
      if (!response.ok) throw new Error("บันทึกการตั้งค่าไม่สำเร็จ");
      setSaved(preferences); applyPreferences(preferences); setFeedback("บันทึกการตั้งค่าแล้ว");
    } catch (reason) {
      applyPreferences(saved); setPreferences(saved);
      setFeedback(reason instanceof Error ? reason.message : "บันทึกการตั้งค่าไม่สำเร็จ"); setIsError(true);
    } finally { setSaving(false); }
  }

  return <ConsolePage icon={Settings2} title="ตั้งค่าหน้าจอ" subtitle="ปรับสี bubble ขนาดตัวอักษร และธีมสำหรับหน้าสนทนา">
      <div className="settings-grid">
        <section className="settings-panel" aria-labelledby="settings-title">
          <h2 id="settings-title">การแสดงผลแชท</h2><p>ค่าที่บันทึกจะใช้กับบัญชีของคุณทุกครั้งที่เข้าสู่ระบบ</p>
          {loading ? <p className="empty-copy">กำลังโหลดการตั้งค่า…</p> : !ready ? <div><p className="load-error" role="alert">{feedback || "โหลดการตั้งค่าไม่สำเร็จ"}</p><button className="setting-choice" type="button" onClick={() => void load()}>ลองอีกครั้ง</button></div> : <>
            <div className="setting-group"><span className="setting-label"><Palette size={15} /> สี bubble ของคุณ</span>
              <div className="color-options" role="group" aria-label="เลือกสี bubble">
                {colors.map((color) => <button key={color} type="button" className="color-choice" style={{ background: color }} aria-label={`เลือกสี ${color}`} aria-pressed={preferences.bubble_color.toLowerCase() === color} onClick={() => update({ ...preferences, bubble_color: color })} />)}
                <label className="custom-color-label" title="เลือกสีอื่น"><span>สีอื่น</span><input className="color-input" type="color" aria-label="เลือกสี bubble แบบกำหนดเอง" value={preferences.bubble_color} onChange={(event) => update({ ...preferences, bubble_color: event.target.value })} /></label>
              </div>
            </div>
            <div className="setting-group"><span className="setting-label"><Type size={15} /> ขนาดตัวอักษรในแชท</span>
              <div className="choice-row" role="group" aria-label="ขนาดตัวอักษร">{sizes.map((size) => <button key={size.value} type="button" className="setting-choice" aria-pressed={preferences.chat_text_size === size.value} onClick={() => update({ ...preferences, chat_text_size: size.value })}>{size.label}</button>)}</div>
            </div>
            <div className="setting-group"><span className="setting-label">ธีมหน้าจอ</span>
              <div className="choice-row" role="group" aria-label="ธีมหน้าจอ">
                {(["light", "dark"] as const).map((theme) => <button key={theme} type="button" className="setting-choice" aria-pressed={preferences.theme_preference === theme} onClick={() => update({ ...preferences, theme_preference: theme })}>{theme === "light" ? "สว่าง" : "มืด"}</button>)}
              </div>
            </div>
            <button type="button" className="setting-save" onClick={() => void save()} disabled={saving || JSON.stringify(preferences) === JSON.stringify(saved)}>{saving ? <LoaderCircle size={16} className="spin" /> : <Check size={16} />} บันทึกการตั้งค่า</button>
            <p className={`setting-feedback${isError ? " is-error" : ""}`} role="status">{feedback}</p>
          </>}
        </section>
        <section className="settings-panel" aria-labelledby="preview-title"><h2 id="preview-title">ตัวอย่าง</h2><p>ตัวอย่าง bubble ตามค่าที่เลือก</p>
          <div className="chat-preview"><div className="preview-assistant">สวัสดีครับ มีอะไรให้ช่วยไหมครับ</div><div className="preview-bubble">ช่วยดูสินค้ารุ่นนี้ให้หน่อยครับ</div></div>
        </section>
      </div>
  </ConsolePage>;
}
