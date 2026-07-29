"use client";
import { useEffect, useState } from "react";

/** Trigger words that mark a chat message as a random-pick request. Matched
 * diacritic-insensitively (so "bốc thăm" and "boc tham" both hit) against the
 * user's text so the lottery animation can start the instant they send it —
 * before the tool result comes back. A false positive is harmless: the spinner
 * is replaced the moment the bot's real reply lands. */
const TRIGGERS = [
  "random",
  "lottery",
  "boc tham",
  "boc",
  "rut tham",
  "chon dai",
  "chon bua",
  "quay so",
  "xo so",
  "lo to",
];

/** Fold a string to lowercase ASCII (strip Vietnamese diacritics, đ→d) so the
 * trigger match is accent-insensitive. */
function fold(text: string): string {
  return text
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "") // strip combining diacritical marks
    .replace(/đ/g, "d"); // đ → d
}

export function looksLikeRandomRequest(text: string | undefined | null): boolean {
  if (!text) return false;
  const norm = fold(text);
  return TRIGGERS.some((t) => norm.includes(t));
}

/** Slot-machine style placeholder shown while a random pick is in flight: the
 * dice spins and a name rapidly cycles through the roster until the tool result
 * arrives and the RandomPickCard replaces it. Purely cosmetic — it never
 * decides or reveals the winner. */
export function LotterySpinner({ names }: { names: string[] }) {
  const [i, setI] = useState(0);

  useEffect(() => {
    if (names.length < 2) return;
    const id = setInterval(() => setI((n) => n + 1), 90);
    return () => clearInterval(id);
  }, [names.length]);

  const current = names.length ? names[i % names.length] : "…";

  return (
    <div
      role="status"
      aria-label="Đang bốc thăm ngẫu nhiên…"
      className="mt-4 flex justify-start"
    >
      <div className="w-full max-w-[85%] rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-4 text-center shadow-sm">
        <div className="text-2xl motion-safe:animate-spin" aria-hidden>
          🎲
        </div>
        <div
          aria-hidden
          className="mt-1 text-lg font-semibold text-[var(--accent-text)] tabular-nums transition-none"
        >
          {current}
        </div>
        <div className="mt-1 text-xs text-[var(--text-secondary)]">Đang bốc thăm…</div>
      </div>
    </div>
  );
}
