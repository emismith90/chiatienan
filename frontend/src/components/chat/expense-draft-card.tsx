"use client";
import { useEffect, useRef, useState } from "react";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import { fmt } from "@/lib/format";
import type { DraftItem, ExpenseDraft } from "@/types/chat";

interface Member { id: number; display_name: string; nickname?: string | null }
type Adjustment = { member: number; amount: number };

/** Provisional per-head over (billed members + guests), honoring the same
 * base the server computes: base = floor((total - Σadjustments) / heads).
 * Display only; the server recomputes authoritatively on commit. */
export function perHead(
  total: number,
  memberCount: number,
  guestCount: number,
  adjustmentsTotal = 0,
): number {
  const heads = memberCount + guestCount;
  return heads > 0 ? Math.floor((total - adjustmentsTotal) / heads) : 0;
}

/** Provisional itemized shares — mirrors `money.prorate_items`: scale each
 * item by total/Σitems, then hand the leftover đồng to the largest remainders
 * (ties by member id) so the shares sum to `total` exactly. Display only; the
 * server recomputes authoritatively on patch and on commit. */
export function prorate(total: number, items: DraftItem[]): Map<number, number> {
  const out = new Map<number, number>();
  const gross = items.reduce((sum, i) => sum + Math.max(0, i.amount), 0);
  if (!items.length || gross <= 0 || total <= 0) {
    items.forEach((i) => out.set(i.member, 0));
    return out;
  }
  items.forEach((i) => out.set(i.member, Math.floor((Math.max(0, i.amount) * total) / gross)));
  let leftover = total - [...out.values()].reduce((a, b) => a + b, 0);
  const byRemainder = [...items].sort((a, b) => {
    const ra = (Math.max(0, a.amount) * total) % gross;
    const rb = (Math.max(0, b.amount) * total) % gross;
    return rb - ra || a.member - b.member;
  });
  for (const i of byRemainder) {
    if (leftover-- <= 0) break;
    out.set(i.member, (out.get(i.member) ?? 0) + 1);
  }
  return out;
}

export function ExpenseDraftCard({
  message, members, roomId,
}: { message: any; members: Member[]; roomId: number }) {
  const att = message.attachments as ExpenseDraft;
  const [editing, setEditing] = useState(false);
  const readonly = att.status !== "pending" && !editing;
  const [payer, setPayer] = useState<number>(att.payer_member_id);
  const [billed, setBilled] = useState<number[]>(att.member_participants ?? []);
  const [guests, setGuests] = useState<string[]>(att.guests ?? []);
  const [total, setTotal] = useState<number>(att.bill_total ?? 0);
  const [adjustments, setAdjustments] = useState<Adjustment[]>(att.adjustments ?? []);
  const [items, setItems] = useState<DraftItem[]>(att.items ?? []);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [dish, setDish] = useState<string>(att.dish ?? "");
  const [initiator, setInitiator] = useState<string>(att.initiator ?? "");
  const [note, setNote] = useState<string>(att.note ?? "");
  const [guestName, setGuestName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<any>(null);
  const skipFirstRun = useRef(true);

  // Debounced PATCH of the editable state so auto-save-on-supersede uses the latest.
  // Skip the very first run: that fires on mount with the data we just loaded
  // from `att`, unchanged, and would PATCH the draft for no reason.
  useEffect(() => {
    if (skipFirstRun.current) {
      skipFirstRun.current = false;
      return;
    }
    if (att.status !== "pending") return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      api.patchDraft(roomId, message.id, {
        payer_member_id: payer, member_participants: billed, guests,
        bill_total: total, adjustments, items,
        dish: dish || null, initiator: initiator || null, note: note || null,
      }).catch(() => {});
    }, 600);
    return () => timer.current && clearTimeout(timer.current);
  }, [payer, billed, guests, total, adjustments, items, dish, initiator, note, att.status, roomId, message.id]);

  const itemized = items.length > 0;

  // In itemized mode every participant needs exactly one item — the server
  // rejects a draft where they drift apart, so keep them in lockstep here.
  const toggle = (id: number) => {
    const drop = billed.includes(id);
    setBilled((b) => (drop ? b.filter((x) => x !== id) : [...b, id]));
    if (!itemized) return;
    setItems((prev) =>
      drop ? prev.filter((i) => i.member !== id) : [...prev, { member: id, amount: 0 }]);
  };
  const addGuest = () => { if (guestName.trim()) { setGuests((g) => [...g, guestName.trim()]); setGuestName(""); } };

  const adjustmentFor = (memberId: number) =>
    adjustments.find((a) => a.member === memberId)?.amount ?? 0;
  const setAdjustmentFor = (memberId: number, amount: number) =>
    setAdjustments((prev) => {
      const rest = prev.filter((a) => a.member !== memberId);
      return amount === 0 ? rest : [...rest, { member: memberId, amount }];
    });

  const adjustmentsSum = adjustments.reduce((sum, a) => sum + a.amount, 0);
  const ph = perHead(total, billed.length, guests.length, adjustmentsSum);
  const hasAdjustments = !itemized && adjustments.some((a) => a.amount !== 0);

  const itemFor = (memberId: number) => items.find((i) => i.member === memberId)?.amount ?? 0;
  const setItemFor = (memberId: number, amount: number) =>
    setItems((prev) => prev.map((i) => (i.member === memberId ? { ...i, amount } : i)));
  const itemsSum = items.reduce((sum, i) => sum + i.amount, 0);
  const shares = itemized ? prorate(total, items) : null;
  const itemGap = total - itemsSum;

  /** Switch modes. Seeding items at the even-split estimate keeps the draft
   * valid (and committable) from the first click; the two encodings are
   * mutually exclusive, so leaving the other one populated would be stale. */
  const setItemized = (on: boolean) => {
    if (on) {
      setAdjustments([]);
      setItems(billed.map((id) => ({ member: id, amount: Math.max(0, ph) })));
    } else {
      setItems([]);
      setAdjustments([]);
    }
  };

  const statusLabel =
    att.status === "committed" ? "Recorded" : att.status === "cancelled" ? "Cancelled" : null;

  return (
    <div className="mt-1 w-full max-w-[95%] rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-3 shadow-sm">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-semibold text-[var(--text-primary)]">Meal draft</span>
        {statusLabel && <span className="text-xs text-[var(--text-secondary)]">{statusLabel}</span>}
      </div>

      <label className="block text-xs text-[var(--text-secondary)]">Payer</label>
      <select disabled={readonly} value={payer} onChange={(e) => setPayer(Number(e.target.value))}
        className="mb-2 w-full rounded-md border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1 text-sm">
        {members.map((m) => <option key={m.id} value={m.id}>{m.display_name}</option>)}
      </select>

      <div className="mb-2 flex flex-wrap gap-1.5">
        {members.map((m) => (
          <button key={m.id} type="button" disabled={readonly} onClick={() => toggle(m.id)}
            className={`rounded-full border px-2.5 py-1 text-xs ${billed.includes(m.id)
              ? "border-[var(--accent-primary)] bg-[var(--accent-primary)] text-white"
              : "border-[var(--border)] text-[var(--text-secondary)]"}`}>
            {m.display_name}
          </button>
        ))}
        {guests.map((g, i) => (
          <span key={`g${i}`} className="inline-flex items-center gap-1 rounded-full border border-dashed border-[var(--border)] px-2.5 py-1 text-xs text-[var(--text-secondary)]">
            {g} (guest)
            {!readonly && <button type="button" onClick={() => setGuests((x) => x.filter((_, j) => j !== i))}>×</button>}
          </span>
        ))}
      </div>

      {/* Guests and per-item shares don't compose yet (the server rejects the
          pair), so don't offer a control that can only lead to a dead end. */}
      {!readonly && !itemized && (
        <div className="mb-2 flex gap-2">
          <input value={guestName} onChange={(e) => setGuestName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), addGuest())}
            placeholder="Add guest…" className="flex-1 rounded-md border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1 text-sm" />
          <button type="button" onClick={addGuest} className="rounded-md border border-[var(--border)] px-2 text-sm">+</button>
        </div>
      )}

      <label className="block text-xs text-[var(--text-secondary)]">Bill total (đ)</label>
      <input type="number" aria-label="Bill total" disabled={readonly} value={total} onChange={(e) => setTotal(Number(e.target.value))}
        className="mb-2 w-full rounded-md border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1 text-sm" />

      <div className="mb-2 grid grid-cols-2 gap-2">
        <input disabled={readonly} value={dish} onChange={(e) => setDish(e.target.value)} placeholder="Dish"
          className="rounded-md border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1 text-sm" />
        <input disabled={readonly} value={initiator} onChange={(e) => setInitiator(e.target.value)} placeholder="Initiator"
          className="rounded-md border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1 text-sm" />
      </div>
      <input disabled={readonly} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (e.g. 'An changed their mind')"
        className="mb-2 w-full rounded-md border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1 text-sm" />

      {!readonly && guests.length === 0 && (
        <label className="mb-2 flex items-center gap-2 text-xs text-[var(--text-secondary)]">
          <input type="checkbox" checked={itemized} onChange={(e) => setItemized(e.target.checked)} />
          Split by item (everyone pays for what they ate)
        </label>
      )}

      {itemized && (
        <div className="mb-2 flex flex-col gap-1.5 rounded-md border border-[var(--border)] p-2">
          <div className="flex items-center justify-between text-xs text-[var(--text-secondary)]">
            <span>Bill price</span><span>Pays</span>
          </div>
          {members.filter((m) => billed.includes(m.id)).map((m) => (
            <div key={m.id} className="flex items-center justify-between gap-2">
              <span className="min-w-0 flex-1 truncate text-xs text-[var(--text-secondary)]">
                {m.display_name}
                {items.find((i) => i.member === m.id)?.label && (
                  <span className="opacity-70"> · {items.find((i) => i.member === m.id)?.label}</span>
                )}
              </span>
              <input type="number" aria-label={`Item price for ${m.display_name}`}
                disabled={readonly} value={itemFor(m.id)}
                onChange={(e) => setItemFor(m.id, Number(e.target.value))}
                className="w-24 rounded-md border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1 text-right text-xs" />
              <span className="w-20 text-right text-xs font-medium text-[var(--text-primary)]">
                {fmt(shares?.get(m.id) ?? 0)}đ
              </span>
            </div>
          ))}
          {itemGap !== 0 && (
            <p className="text-xs text-[var(--text-secondary)]">
              Bill is {fmt(Math.abs(itemGap))}đ {itemGap < 0 ? "below" : "above"} the item prices
              ({fmt(itemsSum)}đ) — the {itemGap < 0 ? "discount" : "fee"} is spread across everyone
              in proportion to what they ordered.
            </p>
          )}
        </div>
      )}

      {!itemized && (
        <>
          <button type="button" onClick={() => setAdvancedOpen((v) => !v)}
            className="mb-2 text-xs font-medium text-[var(--accent-text)]">
            Adjustments (advanced) {advancedOpen ? "▲" : "▼"}
          </button>
          {advancedOpen && (
            <div className="mb-2 flex flex-col gap-1.5 rounded-md border border-[var(--border)] p-2">
              {members.filter((m) => billed.includes(m.id)).map((m) => (
                <div key={m.id} className="flex items-center justify-between gap-2">
                  <span className="text-xs text-[var(--text-secondary)]">{m.display_name}</span>
                  <input type="number" disabled={readonly} value={adjustmentFor(m.id)}
                    onChange={(e) => setAdjustmentFor(m.id, Number(e.target.value))}
                    className="w-24 rounded-md border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1 text-right text-xs" />
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {!itemized && (
        <p className="mb-2 text-xs text-[var(--text-secondary)]">
          Estimate: <span className="font-medium text-[var(--text-primary)]">{fmt(ph)} đ/person</span>
          {guests.length > 0 && ` • ${guests.length} guest(s) paying cash`}
        </p>
      )}
      {hasAdjustments && (
        <p className="mb-2 text-xs text-[var(--text-secondary)]">
          * Members with an adjustment pay the amount above ± their own adjustment.
        </p>
      )}

      {error && (
        <p className="mb-2 text-xs text-[var(--danger)]">{error}</p>
      )}

      {att.status === "pending" && (
        <div className="flex gap-2">
          <button type="button" disabled={busy}
            onClick={() => {
              setBusy(true);
              setError(null);
              api.commitDraft(roomId, message.id)
                .catch((err) => {
                  setError(err instanceof ApiError ? err.message : "Couldn't record, please try again.");
                })
                .finally(() => setBusy(false));
            }}
            className="flex-1 rounded-lg bg-[var(--accent-primary)] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40">
            Record now
          </button>
          <button type="button" disabled={busy}
            onClick={() => {
              setBusy(true);
              setError(null);
              api.cancelDraft(roomId, message.id)
                .catch((err) => {
                  setError(err instanceof ApiError ? err.message : "Couldn't cancel, please try again.");
                })
                .finally(() => setBusy(false));
            }}
            className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text-secondary)]">
            Cancel
          </button>
        </div>
      )}

      {att.status === "committed" && !editing && (
        <button type="button" onClick={() => setEditing(true)}
          className="mt-1 text-xs font-medium text-[var(--accent-text)]">
          Edit
        </button>
      )}
      {att.status === "committed" && editing && (
        <div className="mt-1 flex gap-2">
          <button type="button" disabled={busy}
            onClick={() => {
              setBusy(true); setError(null);
              api.recommitDraft(roomId, message.id, {
                payer_member_id: payer, member_participants: billed, guests,
                bill_total: total, adjustments, items,
                dish: dish || null, initiator: initiator || null, note: note || null,
              })
                .then(() => setEditing(false))
                .catch((err) => setError(err instanceof ApiError ? err.message : "Couldn't save."))
                .finally(() => setBusy(false));
            }}
            className="flex-1 rounded-lg bg-[var(--accent-primary)] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40">
            Save changes
          </button>
          <button type="button" disabled={busy}
            onClick={() => {
              setPayer(att.payer_member_id);
              setBilled(att.member_participants ?? []);
              setGuests(att.guests ?? []);
              setTotal(att.bill_total ?? 0);
              setAdjustments(att.adjustments ?? []);
              setItems(att.items ?? []);
              setDish(att.dish ?? "");
              setInitiator(att.initiator ?? "");
              setNote(att.note ?? "");
              setEditing(false);
              setError(null);
            }}
            className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text-secondary)]">
            Cancel edit
          </button>
        </div>
      )}
    </div>
  );
}
