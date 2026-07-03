/**
 * Small shared React hooks (FE-04) — the fetch / async-op / debounce patterns the modal surfaces
 * (PromptLibrary, Watchlists, AgentBuilder, AlertSheet) each re-implemented.
 */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Wrap an async mutation with `busy` + `error` state. `run(fn)` flips busy on, runs fn, captures any
 * error into `error`, and always flips busy off. Returns fn's result, or undefined if it threw.
 * (Replaces the repeated `setBusy(true); try { … } finally { setBusy(false) }` blocks.)
 */
export function useAsyncOp() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async <T>(fn: () => Promise<T>): Promise<T | undefined> => {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return undefined;
    } finally {
      setBusy(false);
    }
  }, []);
  return { busy, error, run, setError };
}

/**
 * Fetch-on-mount (and on `url` change) with loading/error + a `reload`. JSON only. A stale response
 * (the url changed, or the component unmounted) is ignored. `url = null` skips the fetch.
 */
export function useFetch<T = unknown>(url: string | null): {
  data: T | undefined; loading: boolean; error: string | null; reload: () => void;
} {
  const [data, setData] = useState<T | undefined>(undefined);
  const [loading, setLoading] = useState(!!url);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  useEffect(() => {
    if (!url) { setLoading(false); return; }
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const r = await fetch(url);
        if (!r.ok) throw new Error(`${r.status}`);
        const json = (await r.json()) as T;
        if (!cancelled) setData(json);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [url, tick]);
  return { data, loading, error, reload };
}

/** Debounced copy of `value` — only updates `ms` after the latest change settles. */
export function useDebounce<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setDebounced(value), ms);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [value, ms]);
  return debounced;
}
