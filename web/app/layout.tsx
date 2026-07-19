import "./globals.css";
import type { ReactNode } from "react";
import type { Metadata, Viewport } from "next";

export const metadata: Metadata = {
  title: "Amber",
  description: "출처가 보이는 AI 투자 리서치 — 모든 답 옆에 근거 원문을 하이라이트해서 보여드려요.",
  icons: {
    icon: [
      { url: "/favicon.svg", type: "image/svg+xml" },
      { url: "/icon-32.png", sizes: "32x32", type: "image/png" },
    ],
    apple: "/apple-touch-icon.png",
  },
  manifest: "/site.webmanifest",
  openGraph: {
    title: "Amber",
    description: "출처가 보이는 AI 투자 리서치 — 근거 없는 답은 없어요.",
    images: [{ url: "/og.png", width: 1200, height: 630 }],
  },
};

// Mobile: full-viewport, notch-safe, no accidental zoom-on-input; the app renders as a
// standalone web-app when saved to a phone home screen (the "webview" the user asked for).
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#FBF9F5" },
    { media: "(prefers-color-scheme: dark)", color: "#1A1815" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      <head>
        {/* Two fonts only (brand): Inter Tight (display) + Pretendard (body·Korean,
            dynamic-subset = Korean subsetting). Mono falls back to system ui-monospace. */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link rel="preconnect" href="https://cdn.jsdelivr.net" crossOrigin="" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
        <link
          href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard-dynamic-subset.min.css"
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
