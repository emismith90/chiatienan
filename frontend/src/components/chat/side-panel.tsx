"use client";
import { KnowledgePanel } from "./knowledge-panel";
import { LedgerPanel } from "./ledger-panel";

export type PanelTab = "ledger" | "knowledge";

const TABS: { id: PanelTab; label: string }[] = [
  { id: "ledger", label: "Ledger" },
  { id: "knowledge", label: "Memory" },
];

/**
 * The right-hand panel: the money ledger, and what Phoenix knows.
 *
 * A tab strip rather than a second header button — the header cluster already
 * overran a 320px phone once and had to be given `flex-wrap`. The active tab lives
 * in `RoomView`, so the desktop column and the phone drawer stay in step and
 * reopening the drawer lands where you left it.
 *
 * Both panels stay mounted and the inactive one is hidden, so switching tabs keeps
 * each side's search box, sub-tab and Mine/Group choice. `active` still gates the
 * knowledge *fetch*, so a room that never opens the tab never pays for it.
 */
export function SidePanel({
  roomId, selfId, ledgerVersion, knowledgeVersion, range, onClearRange, tab, onTab,
}: {
  roomId: number;
  selfId: number | null;
  ledgerVersion: number;
  knowledgeVersion: number;
  range?: { from: string; to: string } | null;
  onClearRange?: () => void;
  tab: PanelTab;
  onTab: (t: PanelTab) => void;
}) {
  return (
    <aside className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto p-4">
      <div role="tablist" aria-label="Side panel"
           className="flex shrink-0 overflow-hidden rounded-lg border border-[var(--border)] text-xs">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            type="button"
            onClick={() => onTab(t.id)}
            className={`flex-1 px-2.5 py-1.5 ${
              tab === t.id
                ? "bg-[var(--accent-primary)] font-semibold text-white"
                : "text-[var(--text-secondary)]"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className={`flex min-h-0 flex-1 flex-col ${tab === "ledger" ? "" : "hidden"}`}>
        <LedgerPanel roomId={roomId} selfId={selfId} version={ledgerVersion}
                     range={range} onClearRange={onClearRange} />
      </div>
      <div className={`flex min-h-0 flex-1 flex-col ${tab === "knowledge" ? "" : "hidden"}`}>
        <KnowledgePanel roomId={roomId} version={knowledgeVersion}
                        active={tab === "knowledge"} />
      </div>
    </aside>
  );
}
