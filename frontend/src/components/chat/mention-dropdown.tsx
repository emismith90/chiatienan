"use client";

/** Returns the partial handle being typed at `caret` (text after a boundary "@"),
 * or null if the caret is not in an @-mention. Requires a whitespace, open-paren,
 * or start-of-string boundary before the "@" so an email address like "a@b.com"
 * is never mistaken for a mention. */
export function mentionQuery(text: string, caret: number): string | null {
  const upto = text.slice(0, caret);
  const m = upto.match(/(?:^|[\s(])@([\w-]*)$/);
  return m ? m[1] : null;
}

/** Splices an accepted `@handle` into `text`, replacing the `[start, end)`
 * mention span. Strips any leading word-characters immediately after `end`
 * as the dangling remainder of the same token being completed — accepting
 * "@bo|ot" (caret after "bo", `end` there) yields "@bot ", not "@bot ot".
 * Returns the new text and the caret position right after the insertion. */
export function spliceMention(
  text: string,
  start: number,
  end: number,
  handle: string,
): { next: string; caret: number } {
  const before = text.slice(0, start);
  const after = text.slice(end).replace(/^[\w-]+/, "");
  const insertion = `@${handle}${/^\s/.test(after) ? "" : " "}`;
  return { next: before + insertion + after, caret: before.length + insertion.length };
}

export function MentionDropdown({
  items,
  active,
  onPick,
}: {
  items: string[];
  active: number;
  onPick: (h: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <ul
      role="listbox"
      aria-label="Gợi ý nhắc tên"
      className="absolute bottom-full left-0 mb-1 w-48 overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] shadow-lg"
    >
      {items.map((h, i) => (
        <li key={h} role="option" aria-selected={i === active}>
          <button
            type="button"
            onMouseDown={(e) => {
              e.preventDefault();
              onPick(h);
            }}
            className={`block w-full px-3 py-2 text-left text-sm ${
              i === active
                ? "bg-[var(--bg-base)] text-[var(--accent-text)]"
                : "text-[var(--text-primary)]"
            }`}
          >
            @{h}
          </button>
        </li>
      ))}
    </ul>
  );
}
