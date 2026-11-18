import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Black Shark · ถามตอบสินค้า",
  description: "แชทถามตอบข้อมูลสินค้า Black Shark จากเอกสารที่มี",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="th">
      <body>{children}</body>
    </html>
  );
}
