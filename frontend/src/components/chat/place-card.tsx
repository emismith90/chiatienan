"use client";
import type { KnowledgePlace } from "@/lib/api";
import { Chip, bandLabel, rhythmLabel, shortDate, visitLabel } from "./knowledge-ui";

/** One restaurant, as a tappable summary row.
 *
 * The counts come from the ledger and are shown as text, never as inputs: editing
 * a computed visit count would be inventing history (design D1). What *is* editable
 * lives one tap away, in `PlaceDialog`.
 */
export function PlaceCard({
  place, onOpen,
}: {
  place: KnowledgePlace;
  onOpen: (p: KnowledgePlace) => void;
}) {
  const rhythm = rhythmLabel(place.stats);
  const closed = place.closed_until;

  return (
    <button
      type="button"
      onClick={() => onOpen(place)}
      className={`w-full rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-2.5 text-left transition-colors duration-150 hover:bg-[var(--bg-base)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)] ${
        place.active ? "" : "opacity-55"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-[var(--text-primary)]">
          {place.name}
        </span>
        {place.stats.band && <Chip tone="accent">{bandLabel(place.stats.band)}</Chip>}
      </div>

      <p className="mt-0.5 text-[11px] text-[var(--text-secondary)]">
        {visitLabel(place.stats)}
        {rhythm && ` · ${rhythm}`}
        {place.note_count > 0 &&
          ` · ${place.note_count} note${place.note_count === 1 ? "" : "s"}`}
      </p>

      <div className="mt-1.5 flex flex-wrap items-center gap-1">
        {!place.active && <Chip tone="danger">hidden</Chip>}
        {closed && <Chip tone="danger">closed until {shortDate(closed)}</Chip>}
        {place.untried && <Chip>untried</Chip>}
        {!place.walkable && <Chip>not walkable</Chip>}
        {place.delivery.length > 0 && <Chip>delivery: {place.delivery.join(", ")}</Chip>}
        {place.tags
          .filter((t) => t !== "chưa-thử")
          .map((t) => (
            <Chip key={t}>{t}</Chip>
          ))}
      </div>
    </button>
  );
}
