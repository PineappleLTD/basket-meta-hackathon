import { useEffect, useRef } from "react";

// The API re-prices markets in baskets and holdings every 10 seconds; pages re-read at the same pace.
export const LIVE_POLL_MS = 10_000;

// Calls `fn` every `ms` while the tab is visible, and again on coming back to it.
export function usePoll(fn: () => void, ms = LIVE_POLL_MS) {
  const latest = useRef(fn);
  useEffect(() => {
    latest.current = fn;
  });
  useEffect(() => {
    const tick = () => {
      if (document.visibilityState === "visible") latest.current();
    };
    const timer = setInterval(tick, ms);
    document.addEventListener("visibilitychange", tick);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [ms]);
}
