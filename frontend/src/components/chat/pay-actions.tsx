"use client";
import { useEffect, useMemo, useState } from "react";
import {
  BankApp,
  Platform,
  appById,
  appForBankCode,
  buildDeeplink,
  detectPlatform,
  getPreferredAppId,
  isMobile,
  listApps,
  logoFor,
  parseQrUrl,
  setPreferredAppId,
} from "@/lib/deeplink";
import { getProfile } from "@/lib/rooms-store";

/** Pay-this-transfer actions, shown under a settlement QR to the debtor only.
 *
 * Two affordances, because the QR alone does not solve the phone case: the QR
 * and the bank app live on the same screen, so scanning your own display is
 * impossible.
 *
 *  - "Mở app" hands off to `dl.vietqr.io`, which opens the bank app with the
 *    payee and amount filled in.
 *  - Copy buttons cover every member regardless of bank — 32 of 65 Vietnamese
 *    banks have no app in VietQR's list at all, and not every app that does
 *    actually pre-fills.
 */
export function PayActions({
  qrUrl,
  amount,
  note,
}: {
  qrUrl: string;
  amount: number;
  note: string;
}) {
  const payee = useMemo(() => parseQrUrl(qrUrl), [qrUrl]);
  // Resolved after mount: both the UA and localStorage are client-only, and
  // reading them during render would desync hydration.
  const [platform, setPlatform] = useState<Platform>("other");
  const [appId, setAppId] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);

  useEffect(() => {
    setPlatform(detectPlatform());
    // Best guess, cheapest first: what they paid from last time, else the app
    // for their own bank — people pay from the bank they hold an account with,
    // and the payee's bank says nothing about that.
    const remembered = getPreferredAppId();
    if (remembered && appById(remembered)) {
      setAppId(remembered);
      return;
    }
    setAppId(appForBankCode(getProfile().bank_code)?.appId ?? null);
  }, []);

  if (!payee) return null;

  const app = appById(appId);
  const mobile = isMobile(platform);

  const choose = (picked: BankApp) => {
    setAppId(picked.appId);
    setPreferredAppId(picked.appId);
    setPicking(false);
    window.location.href = buildDeeplink(picked.appId, payee, amount, note);
  };

  return (
    <div className="flex flex-col items-center gap-2">
      {mobile && (
        <div className="flex items-center gap-2">
          {app ? (
            <a
              href={buildDeeplink(app.appId, payee, amount, note)}
              onClick={() => setPreferredAppId(app.appId)}
              className="inline-flex items-center gap-2 rounded-md bg-[var(--accent-primary)] px-3 py-2 text-sm font-medium text-white transition-colors duration-150 hover:bg-[var(--accent-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)]"
            >
              <AppLogo app={app} platform={platform} />
              Mở {app.appName}
            </a>
          ) : (
            <button
              type="button"
              onClick={() => setPicking(true)}
              className="rounded-md bg-[var(--accent-primary)] px-3 py-2 text-sm font-medium text-white transition-colors duration-150 hover:bg-[var(--accent-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)]"
            >
              Mở app ngân hàng
            </button>
          )}
          {app && (
            <button
              type="button"
              onClick={() => setPicking(true)}
              className="rounded-md border border-[var(--border)] px-2.5 py-2 text-xs text-[var(--text-secondary)] transition-colors duration-150 hover:bg-[var(--bg-base)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)]"
            >
              Đổi app
            </button>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-center gap-1.5">
        <CopyChip label="Số TK" value={payee.accountNumber} />
        <CopyChip label="Số tiền" value={String(Math.round(amount))} />
        {note && <CopyChip label="Nội dung" value={note} />}
      </div>

      {picking && (
        <AppPicker platform={platform} onPick={choose} onClose={() => setPicking(false)} />
      )}
    </div>
  );
}

function AppLogo({ app, platform }: { app: BankApp; platform: Platform }) {
  const src = logoFor(app, platform);
  if (!src) return null;
  return (
    // Plain <img>: these are third-party store URLs, and next/image would need
    // every CDN host allow-listed in next.config for no benefit at 20px.
    <img src={src} alt="" aria-hidden className="h-5 w-5 rounded object-contain" />
  );
}

/** Tap to copy one field, with a brief confirmation in place of the label. */
function CopyChip({ label, value }: { label: string; value: string }) {
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!done) return;
    const t = setTimeout(() => setDone(false), 1500);
    return () => clearTimeout(t);
  }, [done]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setDone(true);
    } catch {
      // Clipboard blocked (insecure origin, denied permission) — leave the
      // label alone rather than claiming a copy that did not happen.
    }
  };

  return (
    <button
      type="button"
      onClick={copy}
      aria-label={`Sao chép ${label}`}
      className="inline-flex items-center gap-1 rounded-full border border-[var(--border)] bg-[var(--bg-base)] px-2.5 py-1 text-xs text-[var(--text-secondary)] transition-colors duration-150 hover:bg-[var(--bg-surface)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)]"
    >
      {done ? "✓ Đã chép" : label}
    </button>
  );
}

function AppPicker({
  platform,
  onPick,
  onClose,
}: {
  platform: Platform;
  onPick: (app: BankApp) => void;
  onClose: () => void;
}) {
  const apps = useMemo(() => listApps(platform), [platform]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 sm:items-center"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Chọn app ngân hàng"
        onClick={(e) => e.stopPropagation()}
        className="max-h-[70vh] w-full max-w-sm overflow-y-auto rounded-t-lg border border-[var(--border)] bg-[var(--bg-surface)] p-3 shadow-xl sm:rounded-lg"
      >
        <p className="px-1 pb-2 text-sm font-medium text-[var(--text-primary)]">
          Chọn app ngân hàng
        </p>
        <ul className="space-y-0.5">
          {apps.map((a) => (
            <li key={a.appId}>
              <button
                type="button"
                onClick={() => onPick(a)}
                className="flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left transition-colors duration-150 hover:bg-[var(--bg-base)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)]"
              >
                <AppLogo app={a} platform={platform} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-[var(--text-primary)]">
                    {a.appName}
                  </span>
                  <span className="block truncate text-xs text-[var(--text-secondary)]">
                    {a.bankName}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
        <button
          type="button"
          onClick={onClose}
          className="mt-2 w-full rounded-md border border-[var(--border)] py-2 text-sm text-[var(--text-secondary)] transition-colors duration-150 hover:bg-[var(--bg-base)]"
        >
          Đóng
        </button>
      </div>
    </div>
  );
}
