import type { Metadata } from "next";
import "./globals.css";
import "./console.css";
import { AppShell } from "@/components/AppShell";

export const metadata: Metadata = {
  title: "Black Shark · Support Console",
  description: "ระบบช่วยตอบข้อมูลสินค้า Black Shark สำหรับทีมสนับสนุน",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="th">
      <body><AppShell>{children}</AppShell></body>
    </html>
  );
}
