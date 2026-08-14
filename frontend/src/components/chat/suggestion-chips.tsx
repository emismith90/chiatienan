"use client";

/** Capability suggestions surfaced as tappable chips above the composer.
 * Tapping one prefills the composer with an editable `@phoenix …` message (it
 * never auto-sends), so newcomers discover what the bot can do. `text` is the
 * exact prefill inserted; a trailing space (e.g. "@phoenix I paid ") invites
 * completion, while a full phrase is ready to send as-is. */
export interface Suggestion {
  label: string;
  icon: string;
  text: string;
}

export const SUGGESTIONS: Suggestion[] = [
  { label: "Log an expense", icon: "🍜", text: "@phoenix I paid " },
  { label: "Who pays this week", icon: "🧮", text: "@phoenix who pays this week" },
  { label: "Who do I owe", icon: "💰", text: "@phoenix how much do I owe" },
];

/** The three chips, on one line on a phone and wrapped on a wide screen.
 *
 * They used to wrap everywhere, which cost two rows above the composer on a
 * phone — and unlike the roster they cannot be collapsed behind a tap without
 * defeating them, since being seen without being asked for is the whole job.
 * A single row that scrolls sideways keeps all three reachable in half the
 * height. `no-scrollbar` hides the bar itself; the chips run to the container's
 * edge so there is always a visible cue that the row continues.
 */
export function SuggestionChips({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="no-scrollbar -mx-4 flex items-center gap-1.5 overflow-x-auto px-4 sm:mx-0 sm:flex-wrap sm:overflow-x-visible sm:px-0">
      {SUGGESTIONS.map((s) => (
        <button
          key={s.label}
          type="button"
          onClick={() => onPick(s.text)}
          className="inline-flex min-h-8 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-[var(--border)] bg-[var(--bg-base)] px-3 py-1.5 text-xs text-[var(--text-secondary)] transition-colors duration-150 hover:bg-[var(--bg-surface)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)]"
        >
          <span aria-hidden>{s.icon}</span>
          {s.label}
        </button>
      ))}
    </div>
  );
}
