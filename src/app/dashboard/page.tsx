"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Activity, CalendarDays, KeyRound, MessageCircle, RefreshCw, ShieldCheck, Users } from "lucide-react";

type ActivityRow = { id: number; actor_email: string; action: string; metadata: Record<string, unknown>; created_at: string };
type DashboardData = {
  total_sessions: number;
  sessions_today: number;
  messages_today: number;
  total_messages: number;
  key_count: number;
  sessions_by_day: Array<{ date: string; count: number }>;
  recent_activity: ActivityRow[];
};

const emptyDashboard: DashboardData = {
  total_sessions: 0, sessions_today: 0, messages_today: 0, total_messages: 0, key_count: 0,
  sessions_by_day: [], recent_activity: [],
};

function formatDate(value: string, options: Intl.DateTimeFormatOptions = { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : new Intl.DateTimeFormat("th-TH", options).format(date);
}

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData>(emptyDashboard);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const response = await fetch("/api/admin/dashboard", { cache: "no-store" });
      if (!response.ok) throw new Error("โหลดข้อมูล dashboard ไม่สำเร็จ");
      setData({ ...emptyDashboard, ...await response.json() as DashboardData });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "โหลดข้อมูล dashboard ไม่สำเร็จ");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);
  const maxSessions = Math.max(1, ...data.sessions_by_day.map((item) => item.count));
  const metrics = [
    { label: "แชททั้งหมด", value: data.total_sessions, icon: MessageCircle },
    { label: "แชทวันนี้", value: data.sessions_today, icon: Users },
    { label: "ข้อความวันนี้", value: data.messages_today, icon: Activity },
    { label: "Gemini keys", value: data.key_count, icon: KeyRound },
  ];

  return <main className="admin-page">
    <div className="admin-page-inner">
      <header className="admin-page-header">
        <div><h1>Dashboard</h1><p>ภาพรวมการใช้งานระบบและกิจกรรมล่าสุดของทีม</p></div>
        <button className="header-action" type="button" onClick={() => void load()} disabled={loading}><RefreshCw size={15} /> รีเฟรชข้อมูล</button>
      </header>
      <section className="dashboard-welcome">
        <div><span className="dashboard-kicker"><ShieldCheck size={14} /> พื้นที่ทำงานของทีม</span><h2>ยินดีต้อนรับสู่ Black Shark Console</h2><p>จัดการแชท คีย์สำหรับบอท และการตั้งค่าการแสดงผลได้จากเมนูด้านข้าง</p></div>
        <img src="/mascot/cat-girl-pixel.png" alt="มาสคอตแมวพิกเซล" />
      </section>
      {error && <p className="load-error" role="alert">{error}</p>}
      <section className="metric-grid" aria-label="ตัวเลขสรุป">
        {metrics.map(({ label, value, icon: Icon }) => <article className="metric-block" key={label}>
          <span><Icon size={15} /> {label}</span><strong>{loading ? "…" : value.toLocaleString("th-TH")}</strong>
        </article>)}
      </section>
      <div className="dashboard-lower">
        <section className="panel" aria-labelledby="sessions-title">
          <div className="panel-heading"><div><h2 id="sessions-title">แชทในช่วง 7 วัน</h2><p>จำนวน session ใหม่ในแต่ละวัน</p></div><CalendarDays size={18} /></div>
          {data.sessions_by_day.length ? <div className="trend-chart" role="img" aria-label="กราฟจำนวนแชทในช่วงเจ็ดวัน">
            {data.sessions_by_day.map((item) => <div className="trend-day" key={item.date} title={`${formatDate(item.date, { day: "numeric", month: "long" })}: ${item.count} แชท`}>
              <strong>{item.count}</strong><div className="trend-bar-track"><div className="trend-bar" style={{ height: `${Math.max(2, item.count / maxSessions * 100)}%` }} /></div>
              <span>{formatDate(item.date, { weekday: "short" })}</span>
            </div>)}
          </div> : <p className="empty-copy">{loading ? "กำลังโหลดข้อมูล…" : "ยังไม่มีข้อมูลแชทในช่วงนี้"}</p>}
          <p className="chart-footnote">ข้อความสะสมทั้งหมด {data.total_messages.toLocaleString("th-TH")} รายการ</p>
        </section>
        <section className="panel" aria-labelledby="activity-title">
          <div className="panel-heading"><div><h2 id="activity-title">กิจกรรมล่าสุด</h2><p>รายการจากบันทึกผู้ดูแลระบบ</p></div><Link href="/logs" aria-label="ดูบันทึกทั้งหมด">ดูทั้งหมด</Link></div>
          {data.recent_activity.length ? <div className="activity-list">{data.recent_activity.map((row) => <article className="activity-row" key={row.id}>
            <strong>{row.action}</strong><span>{row.actor_email}</span><time dateTime={row.created_at}>{formatDate(row.created_at)}</time>
          </article>)}</div> : <p className="empty-copy">{loading ? "กำลังโหลดข้อมูล…" : "ยังไม่มีกิจกรรมที่บันทึกไว้"}</p>}
        </section>
      </div>
    </div>
  </main>;
}
