"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw, ScrollText } from "lucide-react";
import { ConsolePage } from "@/components/ConsolePage";

type LogRow = { id: number; actor_email: string; action: string; metadata: Record<string, unknown>; created_at: string };
const actionOptions = ["auth.login", "auth.logout", "preferences.update", "llm_key.add", "llm_key.delete"];

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : new Intl.DateTimeFormat("th-TH", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function formatMetadata(metadata: Record<string, unknown>) {
  const entries = Object.entries(metadata || {});
  return entries.length ? entries.map(([key, value]) => `${key}: ${String(value)}`).join(" · ") : "—";
}

export default function LogsPage() {
  const [rows, setRows] = useState<LogRow[]>([]);
  const [action, setAction] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const query = new URLSearchParams({ limit: "100" });
      if (action) query.set("action", action);
      const response = await fetch(`/api/admin/logs?${query}`, { cache: "no-store" });
      if (!response.ok) throw new Error("โหลดบันทึกระบบไม่สำเร็จ");
      setRows((await response.json() as { logs: LogRow[] }).logs || []);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "โหลดบันทึกระบบไม่สำเร็จ"); }
    finally { setLoading(false); }
  }, [action]);

  useEffect(() => { void load(); }, [load]);

  return <ConsolePage icon={ScrollText} title="บันทึกระบบ" subtitle="ตรวจสอบการเข้าสู่ระบบ การเปลี่ยนค่าหน้าจอ และการจัดการ Gemini keys"
    actions={<button className="header-action" type="button" onClick={() => void load()} disabled={loading}><RefreshCw size={15} /> รีเฟรช</button>}>
      <div className="admin-toolbar"><label htmlFor="log-action">ประเภทกิจกรรม
        <select id="log-action" value={action} onChange={(event) => setAction(event.target.value)}><option value="">ทั้งหมด</option>{actionOptions.map((item) => <option key={item} value={item}>{item}</option>)}</select>
      </label><span className="log-count">{loading ? "กำลังโหลด…" : `${rows.length} รายการล่าสุด`}</span></div>
      {error && <p className="load-error" role="alert">{error}</p>}
      <div className="log-table-wrap"><table className="log-table"><thead><tr><th scope="col">วันเวลา</th><th scope="col">ผู้ใช้</th><th scope="col">กิจกรรม</th><th scope="col">รายละเอียด</th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.id}><td><time dateTime={row.created_at}>{formatDate(row.created_at)}</time></td><td>{row.actor_email}</td><td><span className="log-action">{row.action}</span></td><td><span className="log-meta">{formatMetadata(row.metadata)}</span></td></tr>)}</tbody>
      </table>{!rows.length && <p className="empty-copy log-empty">{loading ? "กำลังโหลดข้อมูล…" : "ไม่พบรายการบันทึก"}</p>}</div>
      <div className="log-mobile-list">{rows.map((row) => <article className="log-mobile-card" key={row.id}>
        <header><strong>{row.action}</strong><time dateTime={row.created_at}>{formatDate(row.created_at)}</time></header>
        <p>{row.actor_email}</p><p>{formatMetadata(row.metadata)}</p>
      </article>)}{!rows.length && <p className="empty-copy">{loading ? "กำลังโหลดข้อมูล…" : "ไม่พบรายการบันทึก"}</p>}</div>
  </ConsolePage>;
}
