"use client";
import { useState } from "react";
import { fmt } from "@/lib/format";
import * as api from "@/lib/api";
import { QrDialog } from "./qr-dialog";

interface Row {
  other_id?: number; creditor_id?: number; debtor_id?: number;
  name: string; meal_id: number; dish: string | null; amount: number; status: string;
}

/** The ⑦ button's state machine, shared by both directions of a row.
 *
 * `record` is what differs: the debtor's row posts "I paid them", the
 * creditor's posts "they paid me". Everything around it — idle → busy → paid,
 * a failure that leaves the row untouched so the tap can be repeated, and the
 * once-only guard — is identical, and used to exist only on the owe side. */
function useMarkPaid(initiallyPaid: boolean, record: () => Promise<unknown>, onPaid?: () => void) {
  const [paid, setPaid] = useState(initiallyPaid);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(false);
  async function mark() {
    if (busy || paid) return;
    setBusy(true);
    setErr(false);
    try {
      await record();
      setPaid(true);
      onPaid?.();
    } catch {
      /* leave as unpaid so the user can retry */
      setErr(true);
    } finally {
      setBusy(false);
    }
  }
  return { paid, busy, err, mark };
}

/** The one button, in either direction. Label stays "Mark paid" both ways —
 * it names the outcome, which is the same fact from either end — and the
 * accessible name says which end it is, since a room can hold both rows for
 * the same person at once. */
function MarkPaidButton({ label, busy, onClick }: {
  label: string; busy: boolean; onClick: () => void;
}) {
  return (
    <button type="button" onClick={onClick} disabled={busy} aria-label={label} title={label}
            className="rounded-full border border-[var(--accent-primary)] px-2.5 py-0.5 text-xs font-semibold text-[var(--accent-text)] transition-colors hover:bg-[var(--bg-base)] disabled:opacity-50">
      {busy ? "…" : "Mark paid"}
    </button>
  );
}

/** Name + amount over dish + actions. Two lines, not one.
 *
 * This row lives both in a chat card and in the 260px ledger panel — which is
 * now the panel's default view — and name + dish + amount + button never fit
 * across that width: the amount used to land on top of the dish. */
function RowShell({ name, amount, dish, paid, partial, err, actions, children }: {
  name: string; amount: number; dish: string | null;
  paid: boolean; partial: boolean; err: boolean;
  actions?: React.ReactNode; children?: React.ReactNode;
}) {
  return (
    <li className="px-3 py-2 text-sm">
      <div className="flex items-baseline justify-between gap-2">
        <span className="min-w-0 truncate text-[var(--text-primary)]">{name}</span>
        <span className="shrink-0 font-medium text-[var(--text-secondary)]">{fmt(amount)} đ</span>
      </div>
      <div className="mt-0.5 flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-xs text-[var(--text-secondary)]">
          {dish || "meal"}{paid && " · paid"}
          {!paid && partial && " · partial"}
        </span>
        <span className="flex shrink-0 items-center gap-2">
          {err && <span className="text-xs font-medium text-[var(--danger)]">Failed — retry</span>}
          {actions}
        </span>
      </div>
      {children}
    </li>
  );
}

function OweRow({ r, roomId, onPaid }: { r: Row; roomId: number; onPaid?: () => void }) {
  const creditorId = r.creditor_id ?? r.other_id!;
  const { paid, busy, err, mark } = useMarkPaid(
    r.status === "paid", () => api.quickPay(roomId, creditorId, r.meal_id), onPaid);
  const [qrBusy, setQrBusy] = useState(false);
  const [qrErr, setQrErr] = useState<string | null>(null);
  const [qr, setQr] = useState<api.QrRequest | null>(null);
  // The QR opens in a dialog on this row, not as a bot card in the chat: it
  // answers a private question ("what do I owe Linh?") where it was asked, and
  // the room is not told. Failures stay on the row — e.g. the creditor has no
  // bank details yet, where the server's 409 detail names the fix.
  async function requestQr() {
    if (qrBusy) return;
    setQrBusy(true);
    setQrErr(null);
    try {
      setQr(await api.requestQr(roomId, creditorId));
    } catch (e) {
      setQrErr(e instanceof Error ? e.message : "QR failed");
    } finally {
      setQrBusy(false);
    }
  }
  return (
    <RowShell name={r.name} amount={r.amount} dish={r.dish}
              paid={paid || r.status === "paid"} partial={r.status === "partial"} err={err}
              actions={
                <>
                  {!paid && (
                    <button type="button" onClick={requestQr} disabled={qrBusy} title="Show the payment QR"
                            className="rounded-full border border-[var(--border)] px-2.5 py-0.5 text-xs font-semibold text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-base)] disabled:opacity-50">
                      {qrBusy ? "…" : "QR"}
                    </button>
                  )}
                  {onPaid && !paid && (
                    <MarkPaidButton label={`Mark paid to ${r.name}`} busy={busy} onClick={mark} />
                  )}
                </>
              }>
      {qrErr && <p className="mt-1 text-xs font-medium text-[var(--danger)]">{qrErr}</p>}
      {qr && <QrDialog qr={qr} onClose={() => setQr(null)} />}
    </RowShell>
  );
}

/** "Owed to you", with the same one-tap settle the debtor's side has.
 *
 * Half of every debt is settled in the other direction — cash across the table,
 * a transfer that landed before anyone opened the app — and until now only the
 * person who owed could record it. The creditor was left asking the bot, or
 * waiting for someone else to tap a button they could see and could not press.
 *
 * No QR here: the QR is for paying someone, and nobody pays a debt owed to
 * them. */
function OwedRow({ r, roomId, onPaid }: { r: Row; roomId: number; onPaid?: () => void }) {
  const debtorId = r.debtor_id ?? r.other_id!;
  const { paid, busy, err, mark } = useMarkPaid(
    r.status === "paid", () => api.quickReceive(roomId, debtorId, r.meal_id), onPaid);
  return (
    <RowShell name={r.name} amount={r.amount} dish={r.dish}
              paid={paid || r.status === "paid"} partial={r.status === "partial"} err={err}
              actions={onPaid && !paid
                ? <MarkPaidButton label={`Mark paid by ${r.name}`} busy={busy} onClick={mark} />
                : null} />
  );
}

/** The two owe/owed sections — and deliberately no total under them.
 *
 * A "Net −54.500đ" line used to close this card. It was the only figure here you
 * could not act on, and with debts in both directions it read as if they had been
 * offset, which the ledger never does. Pass `onPaid` (+ roomId) to enable the ⑦
 * "Mark paid" button on unpaid rows in **both** sections. Used by StatementCard
 * and LedgerPanel. */
export function StatementSections({ owe, owed, roomId, onPaid }: {
  owe: Row[]; owed: Row[]; roomId: number; onPaid?: () => void;
}) {
  if (owe.length === 0 && owed.length === 0) {
    return (
      <p className="mt-2 text-xs text-[var(--text-secondary)]">
        You owe nobody, and nobody owes you.
      </p>
    );
  }
  return (
    <div>
      {owe.length > 0 && (
        <div className="mt-2">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">You owe</p>
          <ul className="mt-1 divide-y divide-[var(--border)] rounded-lg border border-[var(--border)] bg-[var(--bg-base)]">
            {owe.map((r) => <OweRow key={`o${r.meal_id}`} r={r} roomId={roomId} onPaid={onPaid} />)}
          </ul>
        </div>
      )}
      {owed.length > 0 && (
        <div className="mt-2">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Owed to you</p>
          <ul className="mt-1 divide-y divide-[var(--border)] rounded-lg border border-[var(--border)] bg-[var(--bg-base)]">
            {owed.map((r) => <OwedRow key={`d${r.meal_id}`} r={r} roomId={roomId} onPaid={onPaid} />)}
          </ul>
        </div>
      )}
    </div>
  );
}

export function StatementCard({ attachments, roomId }: { attachments: any; roomId: number }) {
  return (
    <div className="mt-3">
      <StatementSections
        owe={attachments.owe ?? []} owed={attachments.owed ?? []}
        roomId={roomId} onPaid={() => {}}
      />
    </div>
  );
}
