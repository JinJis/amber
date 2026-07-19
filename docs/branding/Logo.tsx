"use client";

import { useId } from "react";

/* finnote 로고 — 수면 위 지느러미.
   물결선(waterline)이 그대로 주가 차트로 이어져 끝에서 튀어오르고, 노란 화살촉이 상승을
   가리킨다. 상어 몸통은 그리지 않는다(수면 아래를 대신 읽어주는 서비스). 그래서 지느러미
   밑선은 물결선에서 잘린다 — clipPath로 절단면을 만든다. path 데이터는 소스 오브 트루스:
   "개선"하지 말고 크기·간격만 조정할 것. 색은 브랜드 원색(테마 무관), 다크는 invert 변형을. */

type LogoVariant = "mark" | "lockup" | "compact" | "mono" | "invert" | "app";

type LogoProps = {
  variant?: LogoVariant;
  size?: number;
  className?: string;
};

/* 심볼: 기본 (48px 이상) — 물결 두 마디 + 화살촉 */
function Primary({ size, clip, invert }: { size: number; clip: string; invert: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 120 120" fill="none"
      xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
      <defs>
        <clipPath id={clip}>
          <path d="M6 84 C13.3 78.7 20.7 78.7 28 84 C35.3 89.3 42.7 89.3 50 84 C57.3 78.7 64.7 78.7 72 84 C79.3 89.3 86.7 89.3 94 84 L110 52 L124 52 L124 -20 L-20 -20 L-20 84 Z" />
        </clipPath>
      </defs>
      {/* foam — 라이트에서만 보이는 수면 아래 폼(다크에선 --logo-foam=transparent). invert는 항상 폼 없음 */}
      {!invert && (
        <path d="M6 84 C13.3 78.7 20.7 78.7 28 84 C35.3 89.3 42.7 89.3 50 84 C57.3 78.7 64.7 78.7 72 84 C79.3 89.3 86.7 89.3 94 84 L110 52 L110 118 L6 118 Z"
          fill="var(--logo-foam, #D9F1F7)" />
      )}
      <path d="M32 86 Q42 57 62 38 Q72 29 70 44 Q68 65 85 86 L85 106 L32 106 Z"
        fill={invert ? "var(--paper, #FFFCF6)" : "var(--logo-fin, #0E2A3F)"}
        stroke={invert ? "var(--paper, #FFFCF6)" : "var(--logo-fin, #0E2A3F)"}
        strokeWidth="7" strokeLinejoin="round" clipPath={`url(#${clip})`} />
      {!invert && (
        <path d="M45 78 Q52 59 62 47" fill="none" stroke="#3E6C88" strokeWidth="5.5" strokeLinecap="round" opacity=".4" />
      )}
      <path d="M6 84 C13.3 78.7 20.7 78.7 28 84 C35.3 89.3 42.7 89.3 50 84 C57.3 78.7 64.7 78.7 72 84 C79.3 89.3 86.7 89.3 94 84 L105 61"
        fill="none" stroke={invert ? "var(--ocean-300, #5FD0E4)" : "var(--logo-wave, #1690AE)"}
        strokeWidth="7.5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M112 48 L112.9 65.2 L97.7 57.6 Z" fill="var(--hl-500, #FFC94D)" stroke="var(--hl-500, #FFC94D)" strokeWidth="3" strokeLinejoin="round" />
    </svg>
  );
}

/* 심볼: 축소형 (20–32px) — 물결 한 마디, 선을 굵힘 */
function Compact({ size, clip }: { size: number; clip: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 120 120" fill="none"
      xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
      <defs>
        <clipPath id={clip}>
          <path d="M6 90 C17.5 84.7 29 84.7 40.5 90 C52 95.3 63.5 95.3 75 90 L106 56 L124 56 L124 -20 L-20 -20 L-20 90 Z" />
        </clipPath>
      </defs>
      <path d="M22 90 Q33 60 53 39 Q64 30 62 46 Q59 68 76 90 L76 112 L22 112 Z"
        fill="var(--logo-fin, #0E2A3F)" stroke="var(--logo-fin, #0E2A3F)" strokeWidth="9" strokeLinejoin="round" clipPath={`url(#${clip})`} />
      <path d="M6 90 C17.5 84.7 29 84.7 40.5 90 C52 95.3 63.5 95.3 75 90 L98 65"
        fill="none" stroke="var(--logo-wave, #1690AE)" strokeWidth="10" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M110 52 L105.3 72 L90.5 58.6 Z" fill="var(--hl-500, #FFC94D)" stroke="var(--hl-500, #FFC94D)" strokeWidth="4.5" strokeLinejoin="round" />
    </svg>
  );
}

/* 심볼: 지느러미 단독 (모션·빈 화면·초소형 단색). currentColor로 상속 */
function Fin({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
      <path d="M2.6 21.4 Q7 11 15 3.4 Q19.4 0.2 18 7 Q16 14.4 21.4 21.4 Z" fill="currentColor" />
    </svg>
  );
}

/* 앱 아이콘 — 심해 배경 라운드 사각(변의 23%) + 종이색 지느러미 */
function AppIcon({ size, clip }: { size: number; clip: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 120 120" fill="none"
      xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
      <defs>
        <clipPath id={clip}>
          <path d="M13 88 C19.5 83.3 26 83.3 32.5 88 C39 92.7 45.5 92.7 52 88 C58.5 83.3 65 83.3 71.5 88 C78 92.7 84.5 92.7 91 88 L104 66 L124 66 L124 -20 L-20 -20 L-20 88 Z" />
        </clipPath>
      </defs>
      <rect width="120" height="120" rx="28" fill="var(--ink-800, #0E2A3F)" />
      <path d="M33 88 Q44 58 64 36 Q74 27 72 42 Q69 65 86 88 L86 108 L33 108 Z" fill="var(--paper, #FFFCF6)" clipPath={`url(#${clip})`} />
      <path d="M13 88 C19.5 83.3 26 83.3 32.5 88 C39 92.7 45.5 92.7 52 88 C58.5 83.3 65 83.3 71.5 88 C78 92.7 84.5 92.7 91 88 L99 73"
        fill="none" stroke="var(--ocean-300, #5FD0E4)" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M106 62 L105.9 77 L92.9 69.4 Z" fill="var(--hl-500, #FFC94D)" stroke="var(--hl-500, #FFC94D)" strokeWidth="2.6" strokeLinejoin="round" />
    </svg>
  );
}

export function Logo({ variant = "lockup", size = 32, className }: LogoProps) {
  const clip = "fnclip-" + useId().replace(/:/g, "");

  if (variant === "mono") {
    return (
      <span className={className} role="img" aria-label="finnote">
        <Fin size={size} />
      </span>
    );
  }
  if (variant === "compact") {
    return (
      <span className={className} role="img" aria-label="finnote">
        <Compact size={size} clip={clip} />
      </span>
    );
  }
  if (variant === "app") {
    return (
      <span className={className} role="img" aria-label="finnote">
        <AppIcon size={size} clip={clip} />
      </span>
    );
  }
  if (variant === "mark" || variant === "invert") {
    return (
      <span className={className} role="img" aria-label="finnote">
        <Primary size={size} clip={clip} invert={variant === "invert"} />
      </span>
    );
  }

  // lockup — 지느러미 + "finnote" 워드마크 (fin=오션, note=심해, Nunito 800)
  return (
    <span className={className} role="img" aria-label="finnote"
      style={{ display: "inline-flex", alignItems: "center", gap: size * 0.28 }}>
      <Primary size={size} clip={clip} invert={false} />
      <span aria-hidden="true"
        style={{
          fontFamily: "var(--font-brand)",
          fontWeight: 800,
          fontSize: size * 0.8,
          letterSpacing: "-0.038em",
          lineHeight: 1,
        }}>
        <span style={{ color: "var(--graphic, #1690AE)" }}>fin</span>
        <span style={{ color: "var(--text-primary, #0E2A3F)" }}>note</span>
      </span>
    </span>
  );
}

export default Logo;
