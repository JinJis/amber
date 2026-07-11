"use client";

// One source of truth for "are we on a phone-sized screen". Used to switch the app shell into
// its mobile layout (drawer rail + bottom-sheet evidence panel) — the desktop 3-column grid
// collapses to a single scrolling column. matchMedia so it tracks orientation/resize live and
// stays SSR-safe (defaults to desktop until mounted, avoiding a hydration mismatch flash).

import { useEffect, useState } from "react";

export const MOBILE_QUERY = "(max-width: 720px)";

export function useIsMobile(query: string = MOBILE_QUERY): boolean {
  const [isMobile, setIsMobile] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mql = window.matchMedia(query);
    const on = () => setIsMobile(mql.matches);
    on();
    mql.addEventListener("change", on);
    return () => mql.removeEventListener("change", on);
  }, [query]);
  return isMobile;
}
