"use client";
import { LuckyCard } from "./lucky-draw";

/** Trigger words that mark a chat message as a random-pick request. Matched
 * diacritic-insensitively (so "bốc thăm" and "boc tham" both hit) against the
 * user's text so the lottery animation can start the instant they send it —
 * before the tool result comes back. A false positive is harmless: the spinner
 * is replaced the moment the bot's real reply lands.
 *
 * The entries are Vietnamese on purpose (folded to ASCII): they match what users
 * type in the room — "boc tham"/"rut tham" (draw lots), "chon dai" (pick anyone),
 * "quay so"/"xo so"/"lo to" (lottery/bingo). */
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

/** Is the room waiting on a random pick — is the newest message a person's
 * random-pick request the bot has not answered yet? Only a message a person
 * wrote counts: a bot message (a card, a reply) has no author, and its text can
 * hold a trigger word by accident — a place alias "bock bock" folds to "boc" —
 * which left the reel spinning under a confirm card forever. */
export function awaitingRandomPick(
  msg: { author?: unknown; body?: string | null; error?: unknown; queued?: boolean } | null | undefined,
): boolean {
  return !!msg && msg.author != null && !msg.error && !msg.queued && looksLikeRandomRequest(msg.body);
}

/** Slot-machine placeholder shown while the bot's random pick is in flight: the
 * Lucky Draw reel rolls through the roster at full speed until the tool result
 * arrives and the RandomPickCard (which lands the same reel on the winner)
 * replaces it. Purely cosmetic — it never decides or reveals the winner. */
export function LotterySpinner({ names }: { names: string[] }) {
  return (
    <div role="status" aria-label="Drawing at random…" className="mt-4 flex justify-start">
      <div className="w-full max-w-[85%]">
        <LuckyCard names={names} winner={null} spinning>
          <div className="mt-3 text-xs font-semibold">Drawing…</div>
        </LuckyCard>
      </div>
    </div>
  );
}
