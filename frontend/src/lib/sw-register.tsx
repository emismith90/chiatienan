"use client";
import { useEffect } from "react";

export function ServiceWorkerRegister() {
  useEffect(() => {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker
        .register("/sw.js")
        // An already-installed worker keeps serving its old cache until the
        // browser gets around to re-checking sw.js on its own. Asking on every
        // load is what makes a shipped fix (new icons, new shell) arrive at the
        // next visit instead of whenever.
        .then((reg) => reg.update())
        .catch(() => {});
    }
  }, []);
  return null;
}
