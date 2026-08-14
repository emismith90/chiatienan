"use client";
import { useState } from "react";
import * as api from "@/lib/api";
import type { KnowledgePlace } from "@/lib/api";
import {
  Chip, Field, PanelDialog, fieldClass, primaryButtonClass, quietButtonClass,
  rhythmLabel, visitLabel, writeError,
} from "./knowledge-ui";

/** Add or edit a restaurant.
 *
 * Two things this deliberately does not let you do:
 *
 * - **Change the slug.** It is the `place:` subject in `observations.md`, so
 *   recomputing it from a new name would silently detach every note and standing
 *   rule about the place. The name is free to change; the identity is not, and the
 *   dialog says so rather than hiding the field.
 * - **Edit the stats.** Visit counts and the price band come from the ledger
 *   (design D1) and appear here as a sentence.
 *
 * Hiding and temporary closure are separate controls because they mean different
 * things: `closed_until` self-expires (D11) and is the right answer for "đang sửa
 * quán, tuần sau mở lại"; `active=false` is the permanent one, and it is how you
 * delete a place at all — meals reference the row.
 */
export function PlaceDialog({
  roomId, place, onClose, onSaved,
}: {
  roomId: number;
  /** null = creating a new place. */
  place: KnowledgePlace | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [f, setF] = useState({
    name: place?.name ?? "",
    aliases: (place?.aliases ?? []).join(", "),
    tags: (place?.tags ?? []).join(", "),
    delivery: (place?.delivery ?? []).join(", "),
    address: place?.address ?? "",
    phone: place?.phone ?? "",
    walk_minutes: place?.walk_minutes == null ? "" : String(place.walk_minutes),
    price_hint: place?.price_hint == null ? "" : String(place.price_hint),
    closed_until: place?.closed_until ?? "",
  });
  const [walkable, setWalkable] = useState(place?.walkable ?? true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [confirmHide, setConfirmHide] = useState(false);

  const set = (key: keyof typeof f, value: string) => {
    setErr("");
    setF((prev) => ({ ...prev, [key]: value }));
  };

  const list = (raw: string) => raw.split(",").map((s) => s.trim()).filter(Boolean);
  const num = (raw: string) => (raw.trim() === "" ? null : Number(raw));

  async function save() {
    if (!f.name.trim()) {
      setErr("Quán cần có tên.");
      return;
    }
    setBusy(true);
    setErr("");
    const body = {
      name: f.name.trim(),
      aliases: list(f.aliases),
      tags: list(f.tags),
      delivery: list(f.delivery),
      address: f.address.trim() || null,
      phone: f.phone.trim() || null,
      walkable,
      walk_minutes: num(f.walk_minutes),
      price_hint: num(f.price_hint),
      closed_until: f.closed_until || null,
    };
    try {
      if (place) await api.patchPlace(roomId, place.id, body);
      else await api.createPlace(roomId, body);
      onSaved();
      onClose();
    } catch (e) {
      setErr(writeError(e, "Không lưu được, thử lại nhé."));
    } finally {
      setBusy(false);
    }
  }

  async function run(fn: Promise<unknown>, fail: string) {
    setBusy(true);
    setErr("");
    try {
      await fn;
      onSaved();
      onClose();
    } catch (e) {
      setErr(writeError(e, fail));
    } finally {
      setBusy(false);
    }
  }

  const actions = (
    <>
      {err && <p className="mb-2 text-xs text-[var(--danger)]">{err}</p>}
      <div className="flex gap-2">
        <button type="button" disabled={busy} onClick={save} className={primaryButtonClass}>
          {place ? "Lưu" : "Thêm quán"}
        </button>
        <button type="button" disabled={busy} onClick={onClose} className={quietButtonClass}>
          Huỷ
        </button>
      </div>
    </>
  );

  return (
    <PanelDialog label={place ? `Sửa quán ${place.name}` : "Thêm quán"} onClose={onClose}
                 footer={actions}>
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-base font-semibold text-[var(--text-primary)]">
          {place ? place.name : "Thêm quán"}
        </h3>
        {place?.stats.band && <Chip tone="accent">{place.stats.band}</Chip>}
      </div>

      {place && (
        <div className="mt-1 space-y-0.5">
          {/* Read-only, from the ledger (D1). */}
          <p className="text-xs text-[var(--text-secondary)]">
            {visitLabel(place.stats)}
            {rhythmLabel(place.stats) && ` · ${rhythmLabel(place.stats)}`}
          </p>
          {place.stats.avg_per_head != null && (
            <p className="text-xs text-[var(--text-secondary)]">
              {place.stats.avg_per_head.toLocaleString("vi-VN")}₫/người — tính từ sổ
            </p>
          )}
          <p className="font-mono text-[10px] text-[var(--text-secondary)]">
            {place.slug} · mã định danh, không đổi được
          </p>
        </div>
      )}

      <div className="mt-4 space-y-3">
        <Field label="Tên quán">
          <input className={fieldClass} value={f.name} onChange={(e) => set("name", e.target.value)}
                 placeholder="Cơm gà Thịnh Lơ" />
        </Field>
        <Field label="Tên gọi khác (cách nhau bằng dấu phẩy)">
          <input className={fieldClass} value={f.aliases}
                 onChange={(e) => set("aliases", e.target.value)} placeholder="cơm gà, thịnh lơ" />
        </Field>
        <Field label="Thẻ">
          <input className={fieldClass} value={f.tags} onChange={(e) => set("tags", e.target.value)}
                 placeholder="cơm, gần, nhanh" />
        </Field>
        <Field label="Giao hàng (app nào)">
          <input className={fieldClass} value={f.delivery}
                 onChange={(e) => set("delivery", e.target.value)} placeholder="grab, shopee" />
        </Field>
        <Field label="Địa chỉ">
          <input className={fieldClass} value={f.address}
                 onChange={(e) => set("address", e.target.value)} />
        </Field>
        <Field label="Điện thoại">
          <input className={fieldClass} value={f.phone} inputMode="tel"
                 onChange={(e) => set("phone", e.target.value)} />
        </Field>

        <label className="flex items-center gap-2 text-sm text-[var(--text-primary)]">
          <input type="checkbox" checked={walkable} onChange={(e) => setWalkable(e.target.checked)}
                 className="h-4 w-4 accent-[var(--accent-primary)]" />
          Đi bộ được
        </label>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Phút đi bộ">
            <input className={fieldClass} value={f.walk_minutes} inputMode="numeric"
                   onChange={(e) => set("walk_minutes", e.target.value)} placeholder="mặc định 5" />
          </Field>
          <Field label="Giá ước lượng/người">
            <input className={fieldClass} value={f.price_hint} inputMode="numeric"
                   onChange={(e) => set("price_hint", e.target.value)} placeholder="50000" />
          </Field>
        </div>
        <p className="text-[10px] text-[var(--text-secondary)]">
          Giá ước lượng chỉ dùng khi chưa có bữa nào ghi vào sổ — có bữa thật là sổ nói.
        </p>

        <Field label="Đóng tạm đến ngày">
          <input type="date" className={fieldClass} value={f.closed_until}
                 onChange={(e) => set("closed_until", e.target.value)} />
        </Field>
        <p className="text-[10px] text-[var(--text-secondary)]">
          Tự hết hạn — không cần nhớ mở lại.
        </p>
      </div>

      {place && (
        <div className="mt-4 border-t border-[var(--border)] pt-3">
          {place.active ? (
            confirmHide ? (
              <div className="flex items-center gap-2">
                <p className="flex-1 text-xs text-[var(--text-secondary)]">
                  Ẩn quán này khỏi gợi ý? Bữa đã ghi vẫn giữ nguyên.
                </p>
                <button type="button" disabled={busy}
                        onClick={() => run(api.deletePlace(roomId, place.id), "Không ẩn được.")}
                        className="rounded-lg border border-[var(--danger)] px-3 py-1.5 text-xs text-[var(--danger)] disabled:opacity-40">
                  Ẩn
                </button>
              </div>
            ) : (
              <button type="button" onClick={() => setConfirmHide(true)}
                      className="text-xs text-[var(--danger)]">
                Ẩn quán này
              </button>
            )
          ) : (
            <button type="button" disabled={busy}
                    onClick={() => run(api.patchPlace(roomId, place.id, { active: true }),
                                       "Không mở lại được.")}
                    className="text-xs text-[var(--accent-text)]">
              Mở lại quán này
            </button>
          )}
        </div>
      )}
    </PanelDialog>
  );
}
