/** Extract a room invite token from whatever the user pasted.
 *
 * Invites are shared as full links (`${origin}/join/<token>`), but people also
 * paste just the code, or a link with query/hash noise. Accept all of those and
 * return the bare token, or null if nothing usable is present.
 */
export function parseInviteToken(raw: string): string | null {
  const s = (raw ?? "").trim();
  if (!s) return null;
  // A URL (full or partial) containing /join/<token>.
  const m = s.match(/\/join\/([A-Za-z0-9_-]+)/);
  if (m) return m[1];
  // Otherwise treat the input as a bare token — but only if it looks like one
  // (URL-safe base64 chars), so a stray URL or sentence doesn't slip through.
  if (/^[A-Za-z0-9_-]+$/.test(s)) return s;
  return null;
}
