"use client";

import { useEffect, useState } from "react";
import { ArrowRight, LockKeyhole, ShieldCheck } from "lucide-react";

const errorMessages: Record<string, string> = {
  sso_expired: "เซสชันเข้าสู่ระบบหมดอายุ กรุณาเริ่มใหม่อีกครั้ง",
  sso_failed: "ระบบ SSO ไม่สามารถเข้าสู่ระบบได้ กรุณาลองอีกครั้ง",
  sso_state_invalid: "ยืนยันคำขอเข้าสู่ระบบไม่สำเร็จ กรุณาลองใหม่",
  no_token: "ไม่พบข้อมูลยืนยันตัวตนจากระบบ SSO",
  userinfo_failed: "ตรวจสอบบัญชีจากระบบ SSO ไม่สำเร็จ",
  login_unavailable: "ระบบจัดเก็บเซสชันไม่พร้อมใช้งาน",
  invalid_token: "ข้อมูลบัญชีจากระบบ SSO ไม่ครบถ้วน",
  not_allowed: "บัญชีนี้ยังไม่มีสิทธิ์เข้าใช้งาน",
  signed_out: "ออกจากระบบเรียบร้อยแล้ว",
};

function safeReturnTo(value: string | null) {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("\\") || /[\r\n]/.test(value)) return "/dashboard";
  return value.slice(0, 500);
}

export default function LoginPage() {
  const [returnTo, setReturnTo] = useState("/dashboard");
  const [error, setError] = useState("");
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const target = safeReturnTo(params.get("returnTo"));
    setReturnTo(target);
    setError(errorMessages[params.get("error") || ""] || "");
    let active = true;
    fetch("/api/auth/me", { cache: "no-store" }).then((response) => {
      if (active && response.ok) window.location.replace(target);
    }).catch(() => {}).finally(() => { if (active) setChecking(false); });
    return () => { active = false; };
  }, []);

  const loginHref = `/api/auth/sso/login?return_to=${encodeURIComponent(returnTo)}`;

  return <main className="login-page">
    <section className="login-story" aria-label="Black Shark Support">
      <img className="login-mascot" src="/mascot/cat-girl-pixel.png" alt="มาสคอตแมวพิกเซลของ Black Shark" />
      <div>
        <h1>BLACK SHARK<br />Support Console</h1>
        <p>พื้นที่ทำงานสำหรับทีมสนับสนุนสินค้า พร้อมข้อมูลแชทและเครื่องมือจัดการในที่เดียว</p>
      </div>
    </section>
    <section className="login-card" aria-labelledby="login-title">
      <span className="login-mark"><ShieldCheck size={22} /></span>
      <h2 id="login-title">เข้าสู่ระบบ</h2>
      <p>ใช้บัญชีองค์กรผ่านระบบ SSO เพื่อเข้าใช้งานแชทและหน้าจัดการ</p>
      {error && <p className="login-error" role="alert">{error}</p>}
      <a className="sso-button" href={loginHref} aria-disabled={checking} onClick={(event) => { if (checking) event.preventDefault(); }}>
        <LockKeyhole size={17} /> เข้าสู่ระบบด้วยบัญชีองค์กร <ArrowRight size={17} />
      </a>
      <div className="login-note"><ShieldCheck size={15} /><span>บัญชีที่ยืนยันแล้วจะเข้าใช้งานทุกหน้าได้โดยไม่ต้องใส่รหัสซ้ำ</span></div>
      <small className="login-credit">ภาพประตูโทริอิและมาสคอตใช้ประกอบธีมของระบบ</small>
    </section>
  </main>;
}
