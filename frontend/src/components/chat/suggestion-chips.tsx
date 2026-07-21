"use client";

/** Capability suggestions surfaced as tappable chips above the composer.
 * Tapping one prefills the composer with an editable `@bot …` message (it never
 * auto-sends), so newcomers discover what the bot can do. `text` is the exact
 * prefill inserted; a trailing space (e.g. "@bot I paid ") invites completion,
 * while a full phrase is ready to send as-is. */
export interface Suggestion {
  label: string;
  icon: string;
  text: string;
}

export const SUGGESTIONS: Suggestion[] = [
  { label: "Log an expense", icon: "🍜", text: "@bot I paid " },
  { label: "Who pays this week", icon: "🧮", text: "@bot who pays this week" },
  { label: "My balance", icon: "💰", text: "@bot how much do I owe" },
];

export function SuggestionChips({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {SUGGESTIONS.map((s) => (
        <button
          key={s.label}
          type="button"
          onClick={() => onPick(s.text)}
          className="inline-flex min-h-8 items-center gap-1.5 rounded-full border border-[var(--border)] bg-[var(--bg-base)] px-3 py-1.5 text-xs text-[var(--text-secondary)] transition-colors duration-150 hover:bg-[var(--bg-surface)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)]"
        >
          <span aria-hidden>{s.icon}</span>
          {s.label}
        </button>
      ))}
    </div>
  );
}
