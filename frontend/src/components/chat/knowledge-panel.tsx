"use client";
import { useMemo, useState } from "react";
import { useKnowledge } from "@/hooks/use-knowledge";
import type { KnowledgeNote, KnowledgePlace } from "@/lib/api";
import { SectionLabel } from "./knowledge-ui";
import { MemorySections } from "./memory-sections";
import { NoteDialog } from "./note-dialog";
import { NoteRow } from "./note-row";
import { PlaceCard } from "./place-card";
import { PlaceDialog } from "./place-dialog";

type Tab = "places" | "notes" | "log";

const TABS: { id: Tab; label: string }[] = [
  { id: "places", label: "Quán" },
  { id: "notes", label: "Ghi nhớ" },
  { id: "log", label: "Nhật ký" },
];

/** Everything Phoenix knows, in three tabs — one per store.
 *
 * Not one merged list: a dated observation decays, a standing rule does not, and a
 * place row is inert until someone eats there. Collapsing them would hide exactly
 * the distinction design D4 exists to make.
 *
 * The editors are dialogs, and every save calls `reload()` — the file stores are
 * shared with the turn loop, so the panel re-reads rather than patching its own
 * copy and hoping.
 */
export function KnowledgePanel({
  roomId, version, active,
}: {
  roomId: number;
  version: number;
  /** False while the ledger tab is showing: don't fetch a payload nobody is reading. */
  active: boolean;
}) {
  const { data, loading, reload } = useKnowledge(roomId, version, active);
  const [tab, setTab] = useState<Tab>("places");
  const [query, setQuery] = useState("");
  const [placeEdit, setPlaceEdit] = useState<{ place: KnowledgePlace | null } | null>(null);
  const [noteEdit, setNoteEdit] = useState<
    { note: KnowledgeNote | null; presetSubject?: string } | null
  >(null);
  const [conflict, setConflict] = useState(false);

  const q = query.trim().toLowerCase();

  const places = useMemo(() => {
    const rows = data?.places ?? [];
    if (!q) return rows;
    return rows.filter((p) =>
      [p.name, p.slug, ...p.aliases, ...p.tags].some((s) => s.toLowerCase().includes(q)),
    );
  }, [data?.places, q]);

  /** Notes grouped under the thing they are about, places before people. */
  const groups = useMemo(() => {
    const rows = (data?.observations ?? []).filter(
      (o) => !q || o.text.toLowerCase().includes(q) || o.subject_label.toLowerCase().includes(q),
    );
    const byKey = new Map<string, { label: string; kind: string; rows: KnowledgeNote[] }>();
    for (const o of rows) {
      const g = byKey.get(o.subject_key) ?? {
        label: o.subject_label, kind: o.subject_kind, rows: [],
      };
      g.rows.push(o);
      byKey.set(o.subject_key, g);
    }
    const order = { place: 0, member: 1, unknown: 2 } as Record<string, number>;
    return [...byKey.entries()]
      .map(([key, g]) => ({ key, ...g }))
      .sort((a, b) => (order[a.kind] - order[b.kind]) || a.label.localeCompare(b.label, "vi"));
  }, [data?.observations, q]);

  const counts = {
    places: data?.places.length ?? 0,
    notes: data?.observations.length ?? 0,
    log: data?.memory.sections.length ?? 0,
  };

  function afterWrite() {
    setConflict(false);
    reload();
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      {/* Sub-tabs, one per store. */}
      <div role="tablist" aria-label="Bộ nhớ của Phoenix"
           className="flex overflow-hidden rounded-lg border border-[var(--border)] text-xs">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`flex-1 px-2 py-1 ${
              tab === t.id
                ? "bg-[var(--accent-primary)] font-semibold text-white"
                : "text-[var(--text-secondary)]"
            }`}
          >
            {t.label}
            {counts[t.id] > 0 && (
              <span className="ml-1 opacity-70">{counts[t.id]}</span>
            )}
          </button>
        ))}
      </div>

      {conflict && (
        <p className="text-[11px] text-[var(--danger)]">
          Có người vừa sửa — đã tải lại, xem lại rồi thử tiếp nhé.
        </p>
      )}

      {tab !== "log" && (
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={tab === "places" ? "Tìm quán…" : "Tìm ghi nhớ…"}
          aria-label={tab === "places" ? "Tìm quán" : "Tìm ghi nhớ"}
          className="w-full rounded-md border border-[var(--border)] bg-transparent px-2.5 py-1.5 text-xs text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] focus:outline-none focus-visible:ring-2 ring-[var(--accent-primary)]"
        />
      )}

      {loading && !data ? (
        <p className="text-xs text-[var(--text-secondary)]">Loading…</p>
      ) : (
        <div className="min-h-0 flex-1 space-y-2">
          {tab === "places" && (
            <>
              <button type="button" onClick={() => setPlaceEdit({ place: null })}
                      className="w-full rounded-lg border border-dashed border-[var(--border)] py-1.5 text-xs text-[var(--text-secondary)] transition-colors duration-150 hover:bg-[var(--bg-base)]">
                ＋ Thêm quán
              </button>
              {places.length === 0 ? (
                <p className="text-xs text-[var(--text-secondary)]">
                  {q
                    ? "Không có quán nào khớp."
                    : "Chưa có quán nào. Thêm ở đây, hoặc nói với @phoenix «thêm quán Cơm gà Thịnh Lơ»."}
                </p>
              ) : (
                places.map((p) => (
                  <PlaceCard key={p.id} place={p}
                             onOpen={(place) => setPlaceEdit({ place })} />
                ))
              )}
            </>
          )}

          {tab === "notes" && (
            <>
              <button type="button" onClick={() => setNoteEdit({ note: null })}
                      className="w-full rounded-lg border border-dashed border-[var(--border)] py-1.5 text-xs text-[var(--text-secondary)] transition-colors duration-150 hover:bg-[var(--bg-base)]">
                ＋ Thêm ghi nhớ
              </button>
              {groups.length === 0 ? (
                <p className="text-xs text-[var(--text-secondary)]">
                  {q
                    ? "Không có ghi nhớ nào khớp."
                    : "Chưa có ghi nhớ nào. Thử nói với @phoenix «quán Bé Bự phải gọi trước 11h30»."}
                </p>
              ) : (
                groups.map((g) => (
                  <section key={g.key} className="pt-1">
                    <SectionLabel>
                      {g.kind === "member" ? "👤 " : g.kind === "unknown" ? "⚠️ " : ""}
                      {g.label}
                    </SectionLabel>
                    <div className="space-y-0.5">
                      {/* The id is content-derived, so a hand-edited file holding two identical
                          lines would repeat it — index-suffixed so React still gets unique keys. */}
                      {g.rows.map((n, i) => (
                        <NoteRow key={`${n.id}-${i}`} note={n}
                                 onOpen={(note) => setNoteEdit({ note })} />
                      ))}
                    </div>
                  </section>
                ))
              )}
            </>
          )}

          {tab === "log" && data && (
            <MemorySections roomId={roomId} memory={data.memory} etag={data.etags.memory}
                            onSaved={afterWrite} onConflict={() => setConflict(true)} />
          )}
        </div>
      )}

      {placeEdit && (
        <PlaceDialog roomId={roomId} place={placeEdit.place}
                     onClose={() => setPlaceEdit(null)} onSaved={afterWrite} />
      )}
      {noteEdit && data && (
        <NoteDialog roomId={roomId} note={noteEdit.note} subjects={data.subjects}
                    etag={data.etags.observations} presetSubject={noteEdit.presetSubject}
                    onClose={() => setNoteEdit(null)} onSaved={afterWrite}
                    onConflict={() => setConflict(true)} />
      )}
    </div>
  );
}
