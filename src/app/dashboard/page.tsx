"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Activity, ArrowUpRight, CalendarDays, LayoutDashboard, RefreshCw } from "lucide-react";
import { ConsolePage } from "@/components/ConsolePage";

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
  return <ConsolePage icon={LayoutDashboard} title="แดชบอร์ด" subtitle="ภาพรวมการใช้งานระบบและกิจกรรมล่าสุด"
    actions={<button className="header-action" type="button" onClick={() => void load()} disabled={loading}><RefreshCw size={15} /> รีเฟรชข้อมูล</button>}>
      <section className="dashboard-summary">
        <div className="dashboard-summary-copy">
          <h2>สถานีดูแลบทสนทนา</h2>
          <p>สรุปข้อมูลที่บันทึกไว้ในระบบ พร้อมตรวจสอบกิจกรรมล่าสุดของทีม</p>
          <span className="dashboard-summary-note"><Activity size={14} /> ข้อมูลจริงจากระบบหลังบ้าน</span>
        </div>
        <div className="dashboard-mascot-station">
          <div className="dashboard-mascot-copy"><span>ผู้ช่วยประจำทีม</span><strong>ชาร์คกี้</strong><small>คอยดูแลแชทสินค้า</small></div>
          <img className="dashboard-summary-mascot" src="/mascot/cat-girl-pixel.png" alt="มาสคอตสาวแมวชาร์คกี้" />
        </div>
      </section>
      {error && <p className="load-error" role="alert">{error}</p>}
      <section className="dashboard-metrics" aria-label="ตัวเลขสรุปการใช้งาน">
        <dl>
          <div><dt>บทสนทนาทั้งหมด</dt><dd>{loading ? "—" : data.total_sessions.toLocaleString("th-TH")}</dd></div>
          <div><dt>เริ่มวันนี้</dt><dd>{loading ? "—" : data.sessions_today.toLocaleString("th-TH")}</dd></div>
          <div><dt>ข้อความวันนี้</dt><dd>{loading ? "—" : data.messages_today.toLocaleString("th-TH")}</dd></div>
          <div><dt>Gemini keys</dt><dd>{loading ? "—" : data.key_count.toLocaleString("th-TH")}</dd></div>
        </dl>
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
        <section className="panel dashboard-operations" aria-labelledby="overview-title">
          <div className="panel-heading"><div><h2 id="overview-title">กิจกรรมล่าสุด</h2><p>รายการจากบันทึกผู้ดูแลระบบ</p></div><Link href="/logs" aria-label="ดูบันทึกทั้งหมด">ดูทั้งหมด <ArrowUpRight size={14} aria-hidden="true" /></Link></div>
          {data.recent_activity.length ? <div className="activity-list">{data.recent_activity.map((row) => <article className="activity-row" key={row.id}>
            <time dateTime={row.created_at}>{formatDate(row.created_at)}</time><strong>{row.action}</strong><span>{row.actor_email}</span>
          </article>)}</div> : <p className="empty-copy">{loading ? "กำลังโหลดข้อมูล…" : "ยังไม่มีกิจกรรมที่บันทึกไว้"}</p>}
        </section>
      </div>
  </ConsolePage>;
}
