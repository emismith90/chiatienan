"use client";
import { useEffect, useState } from "react";

/** Reactive `navigator.onLine`. Starts optimistic (true) so SSR/first paint
 * doesn't flash an offline state, then syncs on mount and on online/offline. */
export function useOnline(): boolean {
  const [online, setOnline] = useState(true);
  useEffect(() => {
    const sync = () => setOnline(navigator.onLine !== false);
    sync();
    window.addEventListener("online", sync);
    window.addEventListener("offline", sync);
    return () => {
      window.removeEventListener("online", sync);
      window.removeEventListener("offline", sync);
    };
  }, []);
  return online;
}
