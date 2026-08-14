"use client";
import { useState } from "react";
import * as api from "@/lib/api";
import type { KnowledgePlace } from "@/lib/api";
import { fmt } from "@/lib/format";
import {
  Chip, Field, PanelDialog, bandLabel, fieldClass, primaryButtonClass, quietButtonClass,
  rhythmLabel, visitLabel, writeError,
} from "./knowledge-ui";

/** Add or edit a restaurant.
 *
 * The slug is not a field on this form and never will be: recomputing it from a
 * new name would silently detach every note about the place, so `Save` cannot
 * touch it. Changing it deliberately is a different act with a different
 * confirmation — `renamePlaceSlug` migrates the notes and the pending memo cards
 * along with the row — and it lives behind the identity line rather than in the
 * field list, so nobody performs a migration while editing a phone number.
 *
 * Still not editable here:
 *
 * - **The stats.** Visit counts and the price band come from the ledger
 *   (design D1) and appear as a sentence.
 *
 * Hiding and temporary closure are separate controls because they mean different
 * things: `closed_until` self-expires (D11) and is the right answer for "đang sửa
 * quán, tuần sau mở lại"; `active=false` is the permanent one, and it is how you
 * delete a place at all — meals reference the row.
 */
/** "3 notes and 1 pending card", or "Nothing" — what a rename will carry.
 *
 * Stated as a count before the fact, not reported after it: the notes are the
 * reason the slug was frozen in the first place, and a confirmation that does not
 * say how many are at stake is not a confirmation. */
function movesLabel(place: KnowledgePlace): string {
  const parts: string[] = [];
  if (place.note_count) {
    parts.push(`${place.note_count} note${place.note_count === 1 ? "" : "s"}`);
  }
  if (place.pending_memo_count) {
    parts.push(`${place.pending_memo_count} pending card${
      place.pending_memo_count === 1 ? "" : "s"}`);
  }
  if (!parts.length) return "Nothing";
  return parts.join(" and ");
}

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
  // The rename is its own little flow: open the disclosure, type, confirm.
  const [slugOpen, setSlugOpen] = useState(false);
  const [slugDraft, setSlugDraft] = useState(place?.slug ?? "");
  const [slugErr, setSlugErr] = useState("");
  const [slugDone, setSlugDone] = useState("");

  const set = (key: keyof typeof f, value: string) => {
    setErr("");
    setF((prev) => ({ ...prev, [key]: value }));
  };

  const list = (raw: string) => raw.split(",").map((s) => s.trim()).filter(Boolean);
  const num = (raw: string) => (raw.trim() === "" ? null : Number(raw));

  async function save() {
    if (!f.name.trim()) {
      setErr("A place needs a name.");
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
      setErr(writeError(e, "Could not save — try again."));
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

  async function renameSlug() {
    if (!place) return;
    setBusy(true);
    setSlugErr("");
    try {
      const out = await api.renamePlaceSlug(roomId, place.id, slugDraft);
      // The server normalises what was typed, so report what was actually
      // applied rather than what was in the box.
      setSlugDone(out.changed ? out.slug : "");
      setSlugOpen(false);
      onSaved();
    } catch (e) {
      setSlugErr(writeError(e, "Could not change the identifier."));
    } finally {
      setBusy(false);
    }
  }

  const actions = (
    <>
      {err && <p className="mb-2 text-xs text-[var(--danger)]">{err}</p>}
      <div className="flex gap-2">
        <button type="button" disabled={busy} onClick={save} className={primaryButtonClass}>
          {place ? "Save" : "Add place"}
        </button>
        <button type="button" disabled={busy} onClick={onClose} className={quietButtonClass}>
          Cancel
        </button>
      </div>
    </>
  );

  return (
    <PanelDialog label={place ? `Edit ${place.name}` : "Add place"} onClose={onClose}
                 footer={actions}>
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-base font-semibold text-[var(--text-primary)]">
          {place ? place.name : "Add place"}
        </h3>
        {place?.stats.band && <Chip tone="accent">{bandLabel(place.stats.band)}</Chip>}
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
              {fmt(place.stats.avg_per_head)}₫/head — from the ledger
            </p>
          )}
          <div className="pt-0.5">
            <p className="font-mono text-[10px] text-[var(--text-secondary)]">
              {slugDone || place.slug}
              {!slugOpen && (
                <button type="button" onClick={() => { setSlugOpen(true); setSlugDone(""); }}
                        className="ml-2 font-sans text-[10px] text-[var(--accent-text)]">
                  change
                </button>
              )}
            </p>
            {slugOpen && (
              <div className="mt-1.5 rounded-md border border-[var(--border)] p-2">
                <Field label="New identifier">
                  <input className={fieldClass} value={slugDraft}
                         onChange={(e) => { setSlugDraft(e.target.value); setSlugErr(""); }}
                         placeholder="bun-cha-huong-lien" />
                </Field>
                <p className="mt-1.5 text-[10px] text-[var(--text-secondary)]">
                  {movesLabel(place)} will move to the new identifier. The seed files
                  still name the old one — that is safe, they will keep finding this
                  place, but tidy them up when you next edit them.
                </p>
                {slugErr && (
                  <p className="mt-1.5 text-[10px] text-[var(--danger)]">{slugErr}</p>
                )}
                <div className="mt-2 flex gap-2">
                  <button type="button" disabled={busy || !slugDraft.trim()}
                          onClick={renameSlug}
                          className="rounded-lg bg-[var(--accent-primary)] px-3 py-1 text-xs font-medium text-white disabled:opacity-40">
                    Change identifier
                  </button>
                  <button type="button" disabled={busy}
                          onClick={() => { setSlugOpen(false); setSlugDraft(place.slug); }}
                          className="rounded-lg border border-[var(--border)] px-3 py-1 text-xs text-[var(--text-secondary)]">
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="mt-4 space-y-3">
        <Field label="Name">
          <input className={fieldClass} value={f.name} onChange={(e) => set("name", e.target.value)}
                 placeholder="Cơm gà Thịnh Lơ" />
        </Field>
        <Field label="Other names (comma-separated)">
          <input className={fieldClass} value={f.aliases}
                 onChange={(e) => set("aliases", e.target.value)} placeholder="cơm gà, thịnh lơ" />
        </Field>
        <Field label="Tags">
          <input className={fieldClass} value={f.tags} onChange={(e) => set("tags", e.target.value)}
                 placeholder="cơm, gần, nhanh" />
        </Field>
        <Field label="Delivery apps">
          <input className={fieldClass} value={f.delivery}
                 onChange={(e) => set("delivery", e.target.value)} placeholder="grab, shopee" />
        </Field>
        <Field label="Address">
          <input className={fieldClass} value={f.address}
                 onChange={(e) => set("address", e.target.value)} />
        </Field>
        <Field label="Phone">
          <input className={fieldClass} value={f.phone} inputMode="tel"
                 onChange={(e) => set("phone", e.target.value)} />
        </Field>

        <label className="flex items-center gap-2 text-sm text-[var(--text-primary)]">
          <input type="checkbox" checked={walkable} onChange={(e) => setWalkable(e.target.checked)}
                 className="h-4 w-4 accent-[var(--accent-primary)]" />
          Walkable
        </label>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Walk minutes">
            <input className={fieldClass} value={f.walk_minutes} inputMode="numeric"
                   onChange={(e) => set("walk_minutes", e.target.value)} placeholder="default 5" />
          </Field>
          <Field label="Price estimate per head">
            <input className={fieldClass} value={f.price_hint} inputMode="numeric"
                   onChange={(e) => set("price_hint", e.target.value)} placeholder="50000" />
          </Field>
        </div>
        <p className="text-[10px] text-[var(--text-secondary)]">
          The estimate is only used until a real meal reaches the ledger — after that the
          ledger decides.
        </p>

        <Field label="Temporarily closed until">
          <input type="date" className={fieldClass} value={f.closed_until}
                 onChange={(e) => set("closed_until", e.target.value)} />
        </Field>
        <p className="text-[10px] text-[var(--text-secondary)]">
          Expires on its own — nobody has to remember to reopen it.
        </p>
      </div>

      {place && (
        <div className="mt-4 border-t border-[var(--border)] pt-3">
          {place.active ? (
            confirmHide ? (
              <div className="flex items-center gap-2">
                <p className="flex-1 text-xs text-[var(--text-secondary)]">
                  Hide this place from suggestions? Recorded meals stay as they are.
                </p>
                <button type="button" disabled={busy}
                        onClick={() => run(api.deletePlace(roomId, place.id), "Could not hide it.")}
                        className="rounded-lg border border-[var(--danger)] px-3 py-1.5 text-xs text-[var(--danger)] disabled:opacity-40">
                  Hide
                </button>
              </div>
            ) : (
              <button type="button" onClick={() => setConfirmHide(true)}
                      className="text-xs text-[var(--danger)]">
                Hide this place
              </button>
            )
          ) : (
            <button type="button" disabled={busy}
                    onClick={() => run(api.patchPlace(roomId, place.id, { active: true }),
                                       "Could not unhide it.")}
                    className="text-xs text-[var(--accent-text)]">
              Unhide this place
            </button>
          )}
        </div>
      )}
    </PanelDialog>
  );
}
