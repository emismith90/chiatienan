"use client";
import { useEffect, useState } from "react";

/** Is this an iOS device (iPhone/iPad/iPod)? iPadOS 13+ masquerades as a Mac,
 * so also treat a touch-capable "Macintosh" as iOS. */
function isIos(): boolean {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent;
  if (/iphone|ipad|ipod/i.test(ua)) return true;
  return /Macintosh/.test(ua) && (navigator as any).maxTouchPoints > 1;
}

function isStandalone(): boolean {
  if (typeof window === "undefined") return false;
  return (
    window.matchMedia?.("(display-mode: standalone)").matches ||
    (navigator as any).standalone === true
  );
}

/**
 * "Install app" affordance for the PWA.
 *
 * - Chrome/Android fire `beforeinstallprompt`; we stash it and drive our own
 *   button (the browser's mini-infobar is easy to miss) → native prompt.
 * - iOS Safari NEVER fires that event, so there'd otherwise be no way to
 *   install. We detect iOS and show the same button, which opens a short
 *   "Add to Home Screen" instructions sheet.
 * - Rendered only when installable and not already installed (standalone).
 */
export function InstallButton() {
  const [deferred, setDeferred] = useState<any>(null);
  const [ios, setIos] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);

  useEffect(() => {
    if (isStandalone()) return;
    if (isIos()) setIos(true);

    const onPrompt = (e: Event) => {
      e.preventDefault();
      setDeferred(e);
    };
    const onInstalled = () => {
      setDeferred(null);
      setIos(false);
      setSheetOpen(false);
    };
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  // Nothing to offer: no native prompt captured and not iOS.
  if (!deferred && !ios) return null;

  const onClick = async () => {
    if (deferred) {
      deferred.prompt?.();
      try {
        await deferred.userChoice;
      } finally {
        setDeferred(null);
      }
      return;
    }
    // iOS: no programmatic install — show manual instructions.
    setSheetOpen(true);
  };

  return (
    <>
      <button
        type="button"
        onClick={onClick}
        className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-lg border border-[var(--accent-primary)] px-2.5 py-1.5 text-sm font-medium text-[var(--accent-text)] shadow-sm transition-colors duration-150 hover:bg-[var(--bg-base)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)] sm:px-3"
      >
        <DownloadIcon />
        Install app
      </button>
      {sheetOpen && <IosInstallSheet onClose={() => setSheetOpen(false)} />}
    </>
  );
}

function DownloadIcon() {
  return (
    <svg aria-hidden viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3v12" />
      <path d="m7 10 5 5 5-5" />
      <path d="M5 21h14" />
    </svg>
  );
}

/** iOS Safari "Add to Home Screen" walkthrough. */
function IosInstallSheet({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Install app"
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-xs rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-5 shadow-xl"
      >
        <h2 className="text-base font-semibold text-[var(--text-primary)]">Install this app</h2>
        <p className="mt-1 text-sm text-[var(--text-secondary)]">
          Add it to your Home Screen to open it like a native app.
        </p>
        <ol className="mt-4 space-y-3 text-sm text-[var(--text-primary)]">
          <li className="flex items-center gap-2">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--accent-primary)] text-xs font-semibold text-white">1</span>
            <span className="flex flex-wrap items-center gap-1">
              Tap the Share button
              <ShareIcon />
              in Safari&apos;s toolbar.
            </span>
          </li>
          <li className="flex items-center gap-2">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--accent-primary)] text-xs font-semibold text-white">2</span>
            <span>
              Choose <span className="font-medium">Add to Home Screen</span>.
            </span>
          </li>
        </ol>
        <button
          type="button"
          onClick={onClose}
          className="mt-5 w-full rounded-md border border-[var(--border)] py-2 text-sm text-[var(--text-secondary)] transition-colors duration-150 hover:bg-[var(--bg-base)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)]"
        >
          Got it
        </button>
      </div>
    </div>
  );
}

function ShareIcon() {
  return (
    <svg aria-hidden viewBox="0 0 24 24" className="inline h-4 w-4 align-text-bottom text-[var(--accent-text)]" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3v12" />
      <path d="m8 7 4-4 4 4" />
      <path d="M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7" />
    </svg>
  );
}
