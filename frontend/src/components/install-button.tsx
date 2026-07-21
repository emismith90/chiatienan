"use client";
import { useEffect, useState } from "react";

/** Chrome/Android fires `beforeinstallprompt`; we stash it and expose our own
 * button (the browser's mini-infobar is easy to miss). iOS Safari never fires
 * it, so this simply never appears there — iOS users install via Share → Add to
 * Home Screen. Hidden once installed (display-mode: standalone) or after use. */
export function InstallButton() {
  const [deferred, setDeferred] = useState<any>(null);

  useEffect(() => {
    const standalone =
      window.matchMedia?.("(display-mode: standalone)").matches ||
      (navigator as any).standalone === true;
    if (standalone) return;

    const onPrompt = (e: Event) => {
      e.preventDefault();
      setDeferred(e);
    };
    const onInstalled = () => setDeferred(null);
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  if (!deferred) return null;

  return (
    <button
      type="button"
      onClick={async () => {
        deferred.prompt?.();
        try {
          await deferred.userChoice;
        } finally {
          setDeferred(null);
        }
      }}
      className="shrink-0 whitespace-nowrap rounded-lg border border-[var(--accent-primary)] px-2.5 py-1.5 text-sm font-medium text-[var(--accent-text)] shadow-sm transition-colors duration-150 hover:bg-[var(--bg-base)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)] sm:px-3"
    >
      Cài đặt
    </button>
  );
}
