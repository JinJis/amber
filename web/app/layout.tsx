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
      <body>{children}</body>
    </html>
  );
}
