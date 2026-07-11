"use client";

// Company logo for a ticker — one component used everywhere ticker-related (관심종목, 출처 카드,
// 데스크/물어보기 카드, 아티팩트 헤더). Tries the cached brand image (/api/logos → studio → gateway
// → datasets); on a miss or load error it draws a deterministic MONOGRAM (initials on a colored
// tile) so the UI is always clean — never a broken image, never a fabricated/foreign logo.

import { useEffect, useState } from "react";

// A stable hue from the ticker so the same company always gets the same monogram color.
function hue(seed: string): number {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  return h % 360;
}

// Initials: a Korean name → its first syllable; a latin ticker/name → up to 2 letters.
function initials(ticker: string, name?: string | null): string {
  const src = (name || ticker || "?").trim();
  if (/[가-힣]/.test(src)) return src.replace(/[^가-힣]/g, "").slice(0, 1) || src.slice(0, 1);
  const base = (ticker || src).replace(/\.[A-Za-z]+$/, "");   // drop .KS/.KQ suffix
  const words = src.split(/\s+/).filter(Boolean);
  if (words.length >= 2 && !/^[A-Z]{2,}$/.test(base)) return (words[0][0] + words[1][0]).toUpperCase();
  return base.slice(0, 2).toUpperCase();
}

export function TickerLogo({ market, ticker, name, size = 26, className = "" }: {
  market?: string | null; ticker?: string | null; name?: string | null;
  size?: number; className?: string;
}) {
  const [failed, setFailed] = useState(false);
  // reset when the ticker changes so a re-used slot re-attempts its own logo
  useEffect(() => { setFailed(false); }, [ticker, market]);

  const px = { width: size, height: size, minWidth: size } as const;
  if (!ticker || failed) {
    const label = initials(ticker || "", name);
    return (
      <span className={`ticker-logo mono mg ${className}`} style={{ ...px, fontSize: Math.round(size * 0.42),
        background: `hsl(${hue(ticker || label)} 42% 88%)`, color: `hsl(${hue(ticker || label)} 45% 30%)` }}
        aria-hidden data-testid="ticker-monogram">{label}</span>
    );
  }
  const src = `/api/logos?market=${encodeURIComponent(market || "US")}&ticker=${encodeURIComponent(ticker)}`;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img className={`ticker-logo ${className}`} src={src} style={px} width={size} height={size}
      alt="" loading="lazy" decoding="async" onError={() => setFailed(true)} data-testid="ticker-logo-img" />
  );
}
