"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity, Bot, ChartNoAxesCombined, ChevronRight, KeyRound, LayoutDashboard,
  LogOut, Menu, MessageCircle, Search, Settings2, ShieldCheck, UserRound, X,
} from "lucide-react";

type AdminUser = { email: string; username: string; name: string; expires_at?: string };
type NavItem = { href: string; label: string; icon: typeof LayoutDashboard };

const navItems: NavItem[] = [
  { href: "/dashboard", label: "แดชบอร์ด", icon: LayoutDashboard },
  { href: "/", label: "แชทสินค้า", icon: MessageCircle },
  { href: "/logs", label: "บันทึกระบบ", icon: ChartNoAxesCombined },
  { href: "/settings", label: "ตั้งค่าการแสดงผล", icon: Settings2 },
  { href: "/llm", label: "โมเดลและ API keys", icon: KeyRound },
  { href: "/persona", label: "บุคลิกการตอบ", icon: UserRound },
];

const pageLabels: Array<[string, string]> = [
  ["/dashboard", "แดชบอร์ด"], ["/logs", "บันทึกระบบ"], ["/settings", "ตั้งค่าการแสดงผล"],
  ["/llm", "โมเดลและ API keys"], ["/persona", "บุคลิกการตอบ"], ["/", "แชทสินค้า"],
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [user, setUser] = useState<AdminUser | null>(null);
  const [authState, setAuthState] = useState<"checking" | "ready" | "unavailable">("checking");
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuQuery, setMenuQuery] = useState("");
  const [keyboardOpen, setKeyboardOpen] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  const isLogin = pathname === "/login";

  useEffect(() => {
    if (isLogin) return;
    let active = true;
    fetch("/api/auth/me", { cache: "no-store" }).then(async (response) => {
      if (!active) return;
      if (response.status === 401) {
        const returnTo = `${window.location.pathname}${window.location.search}`;
        router.replace(`/login?returnTo=${encodeURIComponent(returnTo)}`);
        return;
      }
      if (!response.ok) throw new Error("session_unavailable");
      setUser(await response.json() as AdminUser);
      setAuthState("ready");
    }).catch(() => { if (active) setAuthState("unavailable"); });
    return () => { active = false; };
  }, [isLogin, router]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (menuOpen && !dialog.open) dialog.showModal();
    if (!menuOpen && dialog.open) dialog.close();
  }, [menuOpen]);

  useEffect(() => {
    setMenuOpen(false);
    setMenuQuery("");
  }, [pathname]);

  useEffect(() => {
    const viewport = window.visualViewport;
    const phone = window.matchMedia("(max-width: 767px)");
    let baseline = Math.max(window.innerHeight, viewport?.height ?? 0);
    const update = () => {
      const height = viewport?.height ?? window.innerHeight;
      document.documentElement.style.setProperty("--site-vh", `${height}px`);
      const active = document.activeElement;
      const editing = active instanceof HTMLElement && active.matches("input, textarea, select, [contenteditable=true]");
      setKeyboardOpen(phone.matches && editing && baseline - height > 120);
      if (!editing) baseline = Math.max(window.innerHeight, height);
    };
    viewport?.addEventListener("resize", update);
    window.addEventListener("resize", update);
    document.addEventListener("focusin", update);
    const onFocusOut = () => window.setTimeout(update, 100);
    document.addEventListener("focusout", onFocusOut);
    update();
    return () => {
      viewport?.removeEventListener("resize", update);
      window.removeEventListener("resize", update);
      document.removeEventListener("focusin", update);
      document.removeEventListener("focusout", onFocusOut);
      document.documentElement.style.removeProperty("--site-vh");
    };
  }, []);

  useEffect(() => {
    if (authState !== "ready") return;
    fetch("/api/admin/preferences", { cache: "no-store" }).then((response) => response.ok ? response.json() : null)
      .then((preferences) => { if (preferences) applyPreferences(preferences); }).catch(() => {});
  }, [authState]);

  const query = menuQuery.trim().toLocaleLowerCase();
  const filteredItems = useMemo(() => navItems.filter((item) => item.label.toLocaleLowerCase().includes(query)), [query]);

  async function logout() {
    setLogoutError("");
    try {
      const response = await fetch("/api/auth/logout", { method: "POST" });
      if (!response.ok) throw new Error("logout_failed");
      setUser(null);
      router.replace("/login?error=signed_out");
    } catch {
      setLogoutError("ออกจากระบบไม่สำเร็จ กรุณาลองอีกครั้ง");
    }
  }

  if (isLogin) return <>{children}</>;
  if (authState === "checking") return <main className="auth-state" aria-live="polite"><span className="loading-mark" /><p>กำลังตรวจสอบสิทธิ์</p></main>;
  if (authState === "unavailable") return <main className="auth-state"><ShieldCheck size={28} /><p>ตรวจสอบการเข้าสู่ระบบไม่ได้</p><button type="button" onClick={() => window.location.reload()}>ลองอีกครั้ง</button></main>;

  const activeItem = navItems.find((item) => item.href === "/" ? pathname === "/" : pathname === item.href || pathname.startsWith(`${item.href}/`));
  const displayName = user?.name || user?.username || "ผู้ดูแลระบบ";
  const currentPage = pageLabels.find(([path]) => path === pathname)?.[1] ?? activeItem?.label ?? "Black Shark";

  return (
    <div className={`site-shell${keyboardOpen ? " keyboard-open" : ""}`}>
      <header className="app-topbar">
        <Link href="/dashboard" className="app-brand" aria-label="ไปหน้าแดชบอร์ด">
          <span className="app-brand-mark"><Bot size={19} /></span>
          <span className="app-brand-copy"><strong>BLACK SHARK</strong><small>SUPPORT OPERATIONS</small></span>
        </Link>
        <div className="topbar-context" aria-live="polite">
          <span>WORKSPACE</span><i aria-hidden="true" /><strong>{currentPage}</strong>
        </div>
        <div className="topbar-account">
          <span className="topbar-account-mark"><ShieldCheck size={16} /></span>
          <span className="topbar-account-copy"><strong>{displayName}</strong><small>เข้าสู่ระบบผ่าน SSO</small></span>
        </div>
        <button
          type="button"
          className="app-menu-trigger"
          aria-label="เปิดเมนูหลัก"
          aria-controls="primary-navigation"
          aria-haspopup="dialog"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen(true)}
        ><Menu size={18} strokeWidth={2} /><span>เมนู</span></button>
      </header>

      <div className="site-main" data-page={pathname || "/"}>{children}</div>

      <dialog id="primary-navigation" ref={dialogRef} className="nav-menu-dialog" onClose={() => setMenuOpen(false)} onCancel={() => setMenuOpen(false)} onClick={(event) => { if (event.target === dialogRef.current) setMenuOpen(false); }}>
        <div className="menu-dialog-header">
          <div className="menu-title-mark"><Activity size={17} /></div>
          <div><h2>เมนูหลัก</h2><p>เลือกส่วนที่ต้องการใช้งาน</p></div>
          <button type="button" className="menu-close" onClick={() => setMenuOpen(false)} aria-label="ปิดเมนู"><X size={18} /></button>
        </div>
        <label className="menu-search"><Search size={16} /><input value={menuQuery} onChange={(event) => setMenuQuery(event.target.value)} placeholder="ค้นหาเมนู" aria-label="ค้นหาเมนู" /></label>
        <div className="menu-dialog-body">
          {filteredItems.length ? <div className="menu-list-group"><h3>พื้นที่ทำงาน</h3>{filteredItems.map((item) => {
            const Icon = item.icon;
            const active = item.href === "/" ? pathname === "/" : pathname === item.href || pathname.startsWith(`${item.href}/`);
            return <Link key={item.href} href={item.href} className={`menu-link${active ? " is-active" : ""}`} aria-current={active ? "page" : undefined} onClick={() => setMenuOpen(false)}>
              <Icon size={17} /><span>{item.label}</span>{active && <span className="menu-current">หน้านี้</span>}<ChevronRight size={15} />
            </Link>;
          })}</div> : <p className="menu-empty">ไม่พบเมนูที่ค้นหา</p>}
        </div>
        <div className="menu-dialog-footer">
          <Link href="/settings" className="menu-profile" onClick={() => setMenuOpen(false)}>
            <span className="account-avatar">{displayName.slice(0, 1).toLocaleUpperCase()}</span>
            <span className="menu-profile-copy"><strong>{displayName}</strong><small>{user?.email}</small></span>
          </Link>
          <button type="button" className="menu-logout" onClick={() => void logout()}><LogOut size={16} /> ออกจากระบบ</button>
        </div>
        {logoutError && <p className="logout-error" role="alert">{logoutError}</p>}
      </dialog>
    </div>
  );
}

function applyPreferences(preferences: { bubble_color?: string; chat_text_size?: string; theme_preference?: string }) {
  const root = document.documentElement;
  if (preferences.bubble_color) root.style.setProperty("--user-bubble", preferences.bubble_color);
  if (preferences.chat_text_size) root.dataset.chatTextSize = preferences.chat_text_size;
  if (preferences.theme_preference) root.dataset.theme = preferences.theme_preference;
}
