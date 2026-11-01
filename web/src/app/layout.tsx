import type { Metadata, Viewport } from "next";
import { Toaster } from "sonner";
import "./globals.css";
import { ThemeScript } from "@/components/theme-script";
import { AppShell } from "@/components/app-shell";

export const metadata: Metadata = {
  title: "ChatGPT2API · 创作与管理工作空间",
  description: "对话、图像与视频创作，以及账号、任务和服务的统一管理工作空间。",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f7f9" },
    { media: "(prefers-color-scheme: dark)", color: "#11171e" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <ThemeScript />
      </head>
      <body className="antialiased">
        <Toaster position="top-right" richColors closeButton />
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
