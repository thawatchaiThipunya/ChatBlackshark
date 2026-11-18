"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bot, ChartNoAxesCombined, ChevronDown, KeyRound,
  LayoutDashboard, LogOut, MessageCircle, Search, Settings2, ShieldCheck, X,
} from "lucide-react";

type AdminUser = { email: string; username: string; name: string; expires_at?: string };
type NavItem = { href: string; label: string; icon: typeof LayoutDashboard };

const primary: NavItem[] = [
  { href: "/dashboard", label: "หน้าแรก", icon: LayoutDashboard },
  { href: "/", label: "แชท", icon: MessageCircle },
  { href: "/logs", label: "สถิติและบันทึก", icon: ChartNoAxesCombined },
  { href: "/settings", label: "ตั้งค่า", icon: Settings2 },
];
const management: NavItem[] = [
  { href: "/llm", label: "จัดการ Gemini key", icon: KeyRound },
  { href: "/logs", label: "บันทึกระบบ", icon: ChartNoAxesCombined },
  { href: "/settings", label: "ตั้งค่าหน้าจอ", icon: Settings2 },
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
    let active = true;
    if (isLogin) return () => { active = false; };
    setAuthState("checking");
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
    }).catch(() => {
      if (active) setAuthState("unavailable");
    });
    return () => { active = false; };
  }, [isLogin, pathname, router]);

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

  const filteredManagement = useMemo(() => {
    const query = menuQuery.trim().toLocaleLowerCase();
    return query ? management.filter((item) => item.label.toLocaleLowerCase().includes(query)) : management;
  }, [menuQuery]);

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
  if (authState === "checking") return <main className="auth-state"><span className="loading-mark" /><p>กำลังตรวจสอบสิทธิ์</p></main>;
  if (authState === "unavailable") return <main className="auth-state"><ShieldCheck size={28} /><p>ตรวจสอบการเข้าสู่ระบบไม่ได้</p><button type="button" onClick={() => window.location.reload()}>ลองอีกครั้ง</button></main>;

  const isActive = (href: string) => href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
  const displayName = user?.name || user?.username || "ผู้ดูแลระบบ";

  return (
    <div className={`site-shell${keyboardOpen ? " keyboard-open" : ""}`}>
      <aside className="site-sidebar" aria-label="เมนูหลัก">
        <Link href="/dashboard" className="site-brand">
          <span className="site-brand-mark"><Bot size={19} /></span>
          <span><strong>BLACK SHARK</strong><small>SUPPORT CONSOLE</small></span>
        </Link>
        <div className="sidebar-group-label">พื้นที่ทำงาน</div>
        <nav className="sidebar-links">
          {primary.slice(0, 2).map((item) => <SidebarLink key={item.href} item={item} active={isActive(item.href)} />)}
        </nav>
        <div className="sidebar-group-label">ระบบ</div>
        <nav className="sidebar-links">
          {management.slice(0, 3).map((item) => <SidebarLink key={item.href} item={item} active={isActive(item.href)} />)}
        </nav>
        <button type="button" className="sidebar-menu-trigger" onClick={() => setMenuOpen(true)}><Search size={16} /> เมนูทั้งหมด</button>
        <div className="sidebar-account">
          <Link href="/settings" className="account-link" aria-label="เปิดโปรไฟล์และการตั้งค่า">
            <span className="account-avatar">{displayName.slice(0, 1).toLocaleUpperCase()}</span>
            <span className="account-copy"><strong>{displayName}</strong><small>{user?.email}</small></span>
            <ChevronDown size={15} />
          </Link>
          <button type="button" className="sidebar-logout" onClick={() => void logout()}><LogOut size={16} /><span>ออกจากระบบ</span></button>
          {logoutError && <p className="logout-error" role="alert">{logoutError}</p>}
        </div>
      </aside>

        <main className="site-main">
        {children}
        <nav className="mobile-dock" aria-label="แถบนำทาง">
          {primary.slice(0, 2).map((item) => <DockLink key={item.href} item={item} active={isActive(item.href)} />)}
          <button type="button" className="dock-link" onClick={() => setMenuOpen(true)} aria-label="เปิดเมนู"><Search size={19} /><span>เมนู</span></button>
          {primary.slice(2).map((item) => <DockLink key={item.href} item={item} active={isActive(item.href)} />)}
        </nav>
      </main>

      <dialog ref={dialogRef} className="nav-menu-dialog" onClose={() => setMenuOpen(false)} onCancel={() => setMenuOpen(false)} onClick={(event) => { if (event.target === dialogRef.current) setMenuOpen(false); }}>
        <div className="menu-dialog-header"><div><h2>เมนู</h2><p>{displayName}</p></div><button type="button" className="menu-close" onClick={() => setMenuOpen(false)} aria-label="ปิดเมนู"><X size={19} /></button></div>
        <label className="menu-search"><Search size={16} /><input value={menuQuery} onChange={(event) => setMenuQuery(event.target.value)} placeholder="ค้นหาเมนู" aria-label="ค้นหาเมนู" /></label>
        <div className="menu-dialog-body">
          {(() => {
            const query = menuQuery.trim().toLocaleLowerCase();
            const overview = primary.slice(0, 2).filter((item) => item.label.toLocaleLowerCase().includes(query));
            return overview.length || filteredManagement.length ? <>
              {overview.length > 0 && <div className="menu-list-group"><h3>ภาพรวมและแชท</h3>{overview.map((item) => <MenuLink key={item.href} item={item} active={isActive(item.href)} />)}</div>}
              {filteredManagement.length > 0 && <div className="menu-list-group"><h3>จัดการระบบ</h3>{filteredManagement.map((item) => <MenuLink key={item.href} item={item} active={isActive(item.href)} />)}</div>}
            </> : <p className="menu-empty">ไม่พบเมนูที่ค้นหา</p>;
          })()}
        </div>
        <div className="menu-dialog-footer"><Link href="/settings" className="menu-profile"><span className="account-avatar">{displayName.slice(0, 1).toLocaleUpperCase()}</span><span>{user?.email}</span></Link><button type="button" className="menu-logout" onClick={() => void logout()}><LogOut size={16} /> ออกจากระบบ</button></div>
        {logoutError && <p className="logout-error" role="alert">{logoutError}</p>}
      </dialog>
    </div>
  );
}

function SidebarLink({ item, active }: { item: NavItem; active: boolean }) {
  const Icon = item.icon;
  return <Link href={item.href} className={`sidebar-link${active ? " is-active" : ""}`} aria-label={item.label} title={item.label} aria-current={active ? "page" : undefined}><Icon size={18} /><span>{item.label}</span></Link>;
}

function DockLink({ item, active }: { item: NavItem; active: boolean }) {
  const Icon = item.icon;
  return <Link href={item.href} className={`dock-link${active ? " is-active" : ""}`} aria-current={active ? "page" : undefined}><Icon size={19} /><span>{item.label}</span></Link>;
}

function MenuLink({ item, active }: { item: NavItem; active: boolean }) {
  const Icon = item.icon;
  return <Link href={item.href} className={`menu-link${active ? " is-active" : ""}`} aria-current={active ? "page" : undefined}><Icon size={17} /><span>{item.label}</span></Link>;
}

function applyPreferences(preferences: { bubble_color?: string; chat_text_size?: string; theme_preference?: string }) {
  const root = document.documentElement;
  if (preferences.bubble_color) root.style.setProperty("--user-bubble", preferences.bubble_color);
  if (preferences.chat_text_size) root.dataset.chatTextSize = preferences.chat_text_size;
  if (preferences.theme_preference) root.dataset.theme = preferences.theme_preference;
}
