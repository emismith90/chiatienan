"use client";
import { useEffect, useRef, useState } from "react";
import * as api from "@/lib/api";
import type { ExpenseDraft } from "@/types/chat";

interface Member { id: number; display_name: string; nickname?: string | null }
type Adjustment = { member: number; amount: number };
const fmt = (n: number) => new Intl.NumberFormat("vi-VN").format(n);

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

export function ExpenseDraftCard({
  message, members, roomId,
}: { message: any; members: Member[]; roomId: number }) {
  const att = message.attachments as ExpenseDraft;
  const readonly = att.status !== "pending";
  const [payer, setPayer] = useState<number>(att.payer_member_id);
  const [billed, setBilled] = useState<number[]>(att.member_participants ?? []);
  const [guests, setGuests] = useState<string[]>(att.guests ?? []);
  const [total, setTotal] = useState<number>(att.bill_total ?? 0);
  const [adjustments, setAdjustments] = useState<Adjustment[]>(att.adjustments ?? []);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [dish, setDish] = useState<string>(att.dish ?? "");
  const [initiator, setInitiator] = useState<string>(att.initiator ?? "");
  const [note, setNote] = useState<string>(att.note ?? "");
  const [guestName, setGuestName] = useState("");
  const [busy, setBusy] = useState(false);
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
    if (readonly) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      api.patchDraft(roomId, message.id, {
        payer_member_id: payer, member_participants: billed, guests,
        bill_total: total, adjustments,
        dish: dish || null, initiator: initiator || null, note: note || null,
      }).catch(() => {});
    }, 600);
    return () => timer.current && clearTimeout(timer.current);
  }, [payer, billed, guests, total, adjustments, dish, initiator, note, readonly, roomId, message.id]);

  const toggle = (id: number) =>
    setBilled((b) => (b.includes(id) ? b.filter((x) => x !== id) : [...b, id]));
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
  const hasAdjustments = adjustments.some((a) => a.amount !== 0);

  const statusLabel =
    att.status === "committed" ? "Đã ghi sổ" : att.status === "cancelled" ? "Đã huỷ" : null;

  return (
    <div className="mt-1 w-full max-w-[95%] rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-3 shadow-sm">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-semibold text-[var(--text-primary)]">Nháp bữa ăn</span>
        {statusLabel && <span className="text-xs text-[var(--text-secondary)]">{statusLabel}</span>}
      </div>

      <label className="block text-xs text-[var(--text-secondary)]">Người trả</label>
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
            {g} (khách)
            {!readonly && <button type="button" onClick={() => setGuests((x) => x.filter((_, j) => j !== i))}>×</button>}
          </span>
        ))}
      </div>

      {!readonly && (
        <div className="mb-2 flex gap-2">
          <input value={guestName} onChange={(e) => setGuestName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), addGuest())}
            placeholder="Thêm khách…" className="flex-1 rounded-md border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1 text-sm" />
          <button type="button" onClick={addGuest} className="rounded-md border border-[var(--border)] px-2 text-sm">+</button>
        </div>
      )}

      <label className="block text-xs text-[var(--text-secondary)]">Tổng hoá đơn (đ)</label>
      <input type="number" disabled={readonly} value={total} onChange={(e) => setTotal(Number(e.target.value))}
        className="mb-2 w-full rounded-md border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1 text-sm" />

      <div className="mb-2 grid grid-cols-2 gap-2">
        <input disabled={readonly} value={dish} onChange={(e) => setDish(e.target.value)} placeholder="Món ăn"
          className="rounded-md border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1 text-sm" />
        <input disabled={readonly} value={initiator} onChange={(e) => setInitiator(e.target.value)} placeholder="Ai rủ"
          className="rounded-md border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1 text-sm" />
      </div>
      <input disabled={readonly} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Ghi chú (vd 'An đổi ý')"
        className="mb-2 w-full rounded-md border border-[var(--border)] bg-[var(--bg-base)] px-2 py-1 text-sm" />

      <button type="button" onClick={() => setAdvancedOpen((v) => !v)}
        className="mb-2 text-xs font-medium text-[var(--accent-primary)]">
        Điều chỉnh (nâng cao) {advancedOpen ? "▲" : "▼"}
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

      <p className="mb-2 text-xs text-[var(--text-secondary)]">
        Tạm tính: <span className="font-medium text-[var(--text-primary)]">{fmt(ph)} đ/người</span>
        {guests.length > 0 && ` • ${guests.length} khách trả tiền mặt`}
      </p>
      {hasAdjustments && (
        <p className="mb-2 text-xs text-[var(--text-secondary)]">
          * Thành viên có điều chỉnh trả mức trên ± phần điều chỉnh riêng.
        </p>
      )}

      {!readonly && (
        <div className="flex gap-2">
          <button type="button" disabled={busy}
            onClick={() => {
              setBusy(true);
              api.commitDraft(roomId, message.id).catch(() => {}).finally(() => setBusy(false));
            }}
            className="flex-1 rounded-lg bg-[var(--accent-primary)] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40">
            Ghi ngay
          </button>
          <button type="button" disabled={busy}
            onClick={() => {
              setBusy(true);
              api.cancelDraft(roomId, message.id).catch(() => {}).finally(() => setBusy(false));
            }}
            className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text-secondary)]">
            Huỷ
          </button>
        </div>
      )}
    </div>
  );
}
