import "./globals.css";
import type { ReactNode } from "react";
import type { Viewport } from "next";

export const metadata = {
  title: "ValueGraph",
  description: "Ask anything about markets, stocks, news, and the economy — with sources.",
};

// Mobile: full-viewport, notch-safe, no accidental zoom-on-input; the app renders as a
// standalone web-app when saved to a phone home screen (the "webview" the user asked for).
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#F4F4F6" },
    { media: "(prefers-color-scheme: dark)", color: "#111214" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Space+Mono:wght@400;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        {/* UXQ-1: 페인트 전 테마 적용(FOUC 방지) — 쿠키(vg_theme) 우선, 없으면 시스템.
            cookies()를 서버에서 읽으면 전 라우트가 dynamic이 되어 공유 페이지 캐시(V-3)가
            죽으므로 인라인 스크립트로 처리한다. */}
        <script dangerouslySetInnerHTML={{ __html:
          "try{var m=document.cookie.match(/(?:^|; )vg_theme=(light|dark)/);" +
          "if(m)document.documentElement.setAttribute('data-theme',m[1]);}catch(e){}" }} />
        {children}
      </body>
    </html>
  );
}
