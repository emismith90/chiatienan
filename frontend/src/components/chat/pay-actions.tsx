"use client";
import { useEffect, useMemo, useState } from "react";
import {
  BankApp,
  Platform,
  appById,
  appForBankCode,
  detectPlatform,
  getPreferredAppId,
  isMobile,
  listApps,
  logoFor,
  appLaunchUrl,
  parseQrUrl,
  setPreferredAppId,
  storeUrlFor,
} from "@/lib/deeplink";
import { getProfile } from "@/lib/rooms-store";

/** Pay-this-transfer actions, shown under a settlement QR to the debtor only.
 *
 * Two affordances, because the QR alone does not solve the phone case: the QR
 * and the bank app live on the same screen, so scanning your own display is
 * impossible.
 *
 *  - "Mở <app>" opens the payer's own bank app, directly via its scheme. It
 *    opens on the app's own screen: no launch URL we have can carry a transfer
 *    (see `lib/deeplink.ts` — including the one VietQR documents for it, which
 *    no bank app registers).
 *  - "Lưu QR" saves the QR to the camera roll. This is the only route that
 *    fills a transfer in: the payload carries the account, amount and note, and
 *    every bank app can read one from the photo library. Two taps beats typing.
 *  - Copy chips carry the numbers, because the launch cannot. They are also the
 *    only option for the 32 of 65 banks with no app in VietQR's list.
 */
export function PayActions({
  qrUrl,
  amount,
  note,
  payerBankCode,
}: {
  qrUrl: string;
  amount: number;
  note: string;
  /** The payer's own `bank_code` from the room roster — the app they hold an
   * account with, and so the app to open. Authoritative: this used to be read
   * from the localStorage profile cache, which is empty for anyone who signed in
   * on another device or never opened the profile dialog, and that silently fell
   * back to the generic picker every time. */
  payerBankCode?: string | null;
}) {
  const payee = useMemo(() => parseQrUrl(qrUrl), [qrUrl]);
  // Resolved after mount: both the UA and localStorage are client-only, and
  // reading them during render would desync hydration.
  const [platform, setPlatform] = useState<Platform>("other");
  const [appId, setAppId] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  const [saving, setSaving] = useState<"idle" | "busy" | "done" | "error">("idle");

  useEffect(() => {
    setPlatform(detectPlatform());
    // Cheapest first: what they paid from last time, else the bank they
    // registered on their profile, and only then the device's local cache of it.
    // People pay from the bank they hold an account with; the payee's bank says
    // nothing about that and is never consulted here.
    const remembered = getPreferredAppId();
    if (remembered && appById(remembered)) {
      setAppId(remembered);
      return;
    }
    const own = appForBankCode(payerBankCode) ?? appForBankCode(getProfile().bank_code);
    setAppId(own?.appId ?? null);
  }, [payerBankCode]);

  if (!payee) return null;

  const app = appById(appId);
  const mobile = isMobile(platform);

  /** Open `target`, and on iOS follow up with the App Store if nothing took it.
   *
   * Android needs no timer: the `intent://` URL carries a
   * `browser_fallback_url` that Chrome resolves itself. iOS has no equivalent,
   * so we watch instead — if the app opened, this document is backgrounded and
   * loses focus, and the timer's guard fails. Same technique VietQR's own page
   * used, and the reason its 3s budget is worth keeping: shorter and a slow
   * cold start looks like a missing app.
   */
  const launch = (app: BankApp) => {
    const target = appLaunchUrl(app, platform);
    if (!target) return;
    window.location.href = target;
    if (platform !== "ios") return;
    const store = storeUrlFor(app, platform);
    if (!store) return;
    const startedAt = Date.now();
    window.setTimeout(() => {
      if (document.hidden || !document.hasFocus()) return;
      if (Date.now() - startedAt > 3000) return;
      window.location.href = store;
    }, 1500);
  };

  /** Put the QR in the camera roll, so the bank app's scanner can read it.
   *
   * The share sheet where it exists (iOS Safari offers "Save Image" there), a
   * plain download otherwise. A cancelled sheet is not a failure — `share`
   * rejects with AbortError when the user dismisses it, and reporting that as an
   * error would be a lie.
   *
   * `img.vietqr.io` serves `access-control-allow-origin: *`, so the fetch needs
   * no proxy of ours. If that ever changes this breaks loudly here rather than
   * silently saving nothing.
   */
  const saveQr = async () => {
    if (saving === "busy") return;
    setSaving("busy");
    try {
      const res = await fetch(qrUrl);
      if (!res.ok) throw new Error(`QR fetch failed: ${res.status}`);
      const blob = await res.blob();
      const file = new File([blob], `chiatienan-${Math.round(amount)}.png`, {
        type: blob.type || "image/png",
      });
      if (navigator.canShare?.({ files: [file] })) {
        try {
          await navigator.share({ files: [file] });
          setSaving("done");
        } catch (err) {
          setSaving((err as Error)?.name === "AbortError" ? "idle" : "error");
        }
        return;
      }
      const href = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = href;
      a.download = file.name;
      a.click();
      URL.revokeObjectURL(href);
      setSaving("done");
    } catch {
      setSaving("error");
    }
  };

  const choose = (picked: BankApp) => {
    setAppId(picked.appId);
    setPreferredAppId(picked.appId);
    setPicking(false);
    launch(picked);
  };

  return (
    <div className="flex flex-col items-center gap-2">
      {mobile && (
        <div className="flex items-center gap-2">
          {app && appLaunchUrl(app, platform) ? (
            <button
              type="button"
              onClick={() => {
                setPreferredAppId(app.appId);
                launch(app);
              }}
              className="inline-flex items-center gap-2 rounded-md bg-[var(--accent-primary)] px-3 py-2 text-sm font-medium text-white transition-colors duration-150 hover:bg-[var(--accent-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)]"
            >
              <AppLogo app={app} platform={platform} />
              Mở {app.appName}
            </button>
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

      {mobile && (
        <button
          type="button"
          onClick={saveQr}
          disabled={saving === "busy"}
          className="rounded-md border border-[var(--accent-primary)] px-3 py-1.5 text-xs font-semibold text-[var(--accent-text)] transition-colors duration-150 hover:bg-[var(--bg-base)] disabled:opacity-50"
        >
          {saving === "busy" ? "Đang lưu…"
            : saving === "done" ? "✓ Đã lưu — quét trong app"
            : saving === "error" ? "Lưu thất bại — thử lại"
            : "Lưu QR để quét"}
        </button>
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
