"use client";
import { fmt } from "@/lib/format";
import { bankNameFor } from "@/lib/deeplink";
import type { QrRequest } from "@/lib/api";
import { PanelDialog, quietButtonClass } from "./knowledge-ui";
import { PayActions } from "./pay-actions";

/** `"2026-07-21"` → `"21/7"` — the day-first form the note itself uses. */
function shortDay(iso: string): string {
  const [, m, d] = iso.split("-");
  return m && d ? `${Number(d)}/${Number(m)}` : iso;
}

/**
 * The payment QR for everything you owe one person, opened from the ledger's QR
 * button.
 *
 * This used to be a bot card posted into the chat. It is a private lookup —
 * nobody else in the room needs to watch you check what you owe — and the thread
 * collected one live QR card per tap, each of which had to be retired again once
 * the debt was paid. A dialog answers the question where it was asked, costs the
 * room nothing, and closes.
 *
 * Everything shown here is computed server-side and passed through untouched
 * (design D3): the amount, the note baked into the QR, and the meals behind the
 * total — listed so the figure can be checked before it is scanned, not after.
 */
export function QrDialog({ qr, onClose }: { qr: QrRequest; onClose: () => void }) {
  const bank = bankNameFor(qr.to.bank_code);
  return (
    <PanelDialog
      label={`Pay ${qr.to.name}`}
      onClose={onClose}
      footer={
        <button type="button" onClick={onClose} className={`w-full ${quietButtonClass}`}>
          Close
        </button>
      }
    >
      <div className="text-center">
        <h3 className="text-base font-semibold text-[var(--text-primary)]">
          Pay {qr.to.name}
        </h3>
        <p className="mt-1 text-2xl font-semibold text-[var(--accent-text)]">
          {fmt(qr.amount)} đ
        </p>
        {/* Everything owed to this one person, not just the row that was tapped
            — so say so, or the total reads as the wrong number. The meals it is
            made of are listed below. */}
        <p className="text-[11px] text-[var(--text-secondary)]">
          everything you owe {qr.to.name}
        </p>
      </div>

      {/* Plain <img>: the URL is a third-party VietQR host, and next/image would
          need it allow-listed in next.config for no gain at this size. */}
      <img
        src={qr.qr_url}
        alt={`QR to transfer ${fmt(qr.amount)} đ to ${qr.to.name}`}
        width={220}
        height={220}
        className="mx-auto mt-3 h-[220px] w-[220px] rounded-lg border border-[var(--border)] bg-white object-contain p-2"
      />

      <div className="mt-3">
        <PayActions
          qrUrl={qr.qr_url}
          amount={qr.amount}
          note={qr.note}
          payerBankCode={qr.from.bank_code}
        />
      </div>

      <dl className="mt-4 space-y-1 border-t border-[var(--border)] pt-3 text-xs">
        {qr.to.account_holder && (
          <Line label="Account name" value={qr.to.account_holder} />
        )}
        {qr.to.account_number && (
          <Line label="Account" value={`${qr.to.account_number}${bank ? ` · ${bank}` : ""}`} />
        )}
        {/* Stacked: the note names every meal, so on a 320px dialog it needs the
            full width rather than whatever the label leaves it. */}
        {qr.note && <Line label="Reference" value={qr.note} stack />}
      </dl>

      {qr.meals.length > 0 && (
        <div className="mt-3 border-t border-[var(--border)] pt-3">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
            Covers {qr.meals.length} meal{qr.meals.length === 1 ? "" : "s"}
          </p>
          <ul className="mt-1 space-y-0.5">
            {qr.meals.map((m) => (
              <li key={m.meal_id} className="flex items-baseline justify-between gap-2 text-xs">
                <span className="min-w-0 truncate text-[var(--text-secondary)]">
                  {shortDay(m.date)} · {m.dish || "meal"}
                </span>
                <span className="shrink-0 text-[var(--text-primary)]">{fmt(m.amount)} đ</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="mt-3 text-[10px] text-[var(--text-secondary)]">
        Transferring does not clear the debt on its own — tap “Mark paid” once the
        money is out.
      </p>
    </PanelDialog>
  );
}

function Line({ label, value, stack }: { label: string; value: string; stack?: boolean }) {
  if (stack) {
    return (
      <div>
        <dt className="text-[var(--text-secondary)]">{label}</dt>
        <dd className="break-words font-medium text-[var(--text-primary)]">{value}</dd>
      </div>
    );
  }
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="shrink-0 text-[var(--text-secondary)]">{label}</dt>
      <dd className="min-w-0 break-all text-right font-medium text-[var(--text-primary)]">
        {value}
      </dd>
    </div>
  );
}
