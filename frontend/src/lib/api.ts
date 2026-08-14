import { parseSSE } from "./sse";
import { activeRoom } from "./rooms-store";

/** Token for the active (most recently accessed) room — see rooms-store. */
export const getToken = (): string | null => activeRoom()?.token ?? null;

export class ApiError extends Error {
  constructor(
    public status: number,
    msg: string,
  ) {
    super(msg);
    this.name = "ApiError";
  }
}

async function req(path: string, init: RequestInit = {}) {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    ...(init.headers as any),
  };
  const tok = getToken();
  if (tok) headers.Authorization = `Bearer ${tok}`;
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}) as any);
    throw new ApiError(res.status, body.detail || res.statusText);
  }
  return res.status === 204 ? null : res.json();
}

export const roomInfo = (t: string) => req(`/api/rooms/${t}`);

/** The bot's @-mention handle. Scope is bot-only for now, so this is a stable
 * constant rather than a network round-trip; swap for a real lookup if
 * multiple bot handles are ever configured. */
export const botHandle = async (): Promise<string> => "phoenix";

export const createAccount = (t: string, b: any) =>
  req(`/api/rooms/${t}/accounts`, { method: "POST", body: JSON.stringify(b) });

export const identify = (t: string, b: any) =>
  req(`/api/rooms/${t}/identify`, { method: "POST", body: JSON.stringify(b) });

export const createRoom = (b: {
  room_name: string;
  display_name: string;
  nickname: string;
  pin: string;
  bank_code?: string;
  account_number?: string;
  account_holder?: string;
}): Promise<{
  token: string;
  room_id: number;
  room_name: string;
  member_id: number;
  invite_token: string;
}> => req(`/api/rooms/create`, { method: "POST", body: JSON.stringify(b) });

export const getMe = () => req(`/api/me`);

export const updateMe = (b: any) => req(`/api/me`, { method: "PUT", body: JSON.stringify(b) });

export const getMembers = (roomId: number) => req(`/api/rooms/${roomId}/members`);

/** One open debt, one direction. There is no net/balance figure anywhere in the
 * API: if A owes B and B owes A, both rows are here and neither cancels. */
export type OutstandingRow = {
  debtor_id: number; debtor_name: string;
  creditor_id: number; creditor_name: string;
  amount: number;
};

export type TimelineEvent =
  | {
      kind: "meal"; meal_id: number; payer_id: number; payer_name: string;
      dish: string | null; occurred_on: string; total: number;
      participant_ids: number[]; created_at: string;
    }
  | {
      kind: "payment"; payment_id: number; from_id: number; to_id: number;
      from_name: string; to_name: string; amount: number;
      occurred_on: string; created_at: string;
    };

export type StatementRow = {
  other_id: number; name: string; meal_id: number; dish: string | null;
  occurred_on: string; amount: number; status: string;
};

export type LedgerData = {
  period: { from: string | null; to: string; keyword: string };
  outstanding: OutstandingRow[];
  timeline: TimelineEvent[];
  me: { owe: StatementRow[]; owed: StatementRow[] };
};

/** `range` scopes the ledger to explicit dates, so a "last week, day by day"
 * question can open the panel on exactly the range that was asked about. */
export const getLedger = (
  roomId: number,
  period = "since_last",
  range?: { from: string; to: string } | null,
): Promise<LedgerData> =>
  req(
    range
      ? `/api/rooms/${roomId}/ledger?from=${range.from}&to=${range.to}`
      : `/api/rooms/${roomId}/ledger?period=${period}`,
  );

// ------------------------------------------------- knowledge & memory panel

/** Everything the ledger derives about one place. **Read-only, all of it** —
 * design D1 computes these in Python precisely so nobody hand-types them. */
export type PlaceStats = {
  times: number;
  last_on: string | null;
  days_since: number | null;
  /** Average VND per head from real meals, or the seed `price_hint` as a fallback. */
  avg_per_head: number | null;
  /** A tertile across this room's own places, not absolute VND. */
  band: string | null;
  weekday_counts: Record<string, number>;
};

export type KnowledgePlace = {
  id: number;
  /** Identity, and the `place:` subject in the observations file. Not editable as a
   * field — changing it is a migration, see `renamePlaceSlug`. */
  slug: string;
  /** Slugs this place used to have, oldest first. Read-only. The seed files keep
   * finding the row through these, which is why a rename does not duplicate it. */
  former_slugs: string[];
  name: string;
  aliases: string[];
  tags: string[];
  delivery: string[];
  address: string | null;
  phone: string | null;
  walkable: boolean;
  walk_minutes: number | null;
  price_hint: number | null;
  closed_until: string | null;
  active: boolean;
  untried: boolean;
  note_count: number;
  /** Memo cards still awaiting Confirm. They carry a frozen `place:<slug>`, so a
   * rename has to move them too — and the confirmation says how many. */
  pending_memo_count: number;
  stats: PlaceStats;
};

export type GateKind = "busy" | "order-by" | "closes";

/** One line of `observations.md`, parsed and labelled by the server. `standing`
 * lines are rules and never age out; dated ones decay, and `stale` marks the ones
 * already past the window the agent reads. */
export type KnowledgeNote = {
  id: string;
  when: string | null;
  standing: boolean;
  subject: string;
  subject_key: string;
  subject_kind: "place" | "member" | "unknown";
  subject_label: string;
  /** The place/member row id, or null for an orphaned subject. Lets a caller that
   * already holds a member find *their* notes without re-folding names. */
  subject_id: number | null;
  gate: string | null;
  gate_kind: GateKind | null;
  gate_label: string | null;
  text: string;
  stale: boolean;
};

export type MemorySection = {
  index: number;
  title: string;
  date: string | null;
  body: string;
};

export type KnowledgeData = {
  /** Fingerprints of the two file-backed stores; handed back on write so a stale
   * panel is refused (409) instead of clobbering a concurrent agent write. */
  etags: { observations: string; memory: string };
  places: KnowledgePlace[];
  observations: KnowledgeNote[];
  memory: {
    sections: MemorySection[];
    watermark: { through_id: number | null; through_at: string | null };
  };
  /** What a new note can be filed against — the editor's picker. */
  subjects: { subject: string; label: string; kind: "place" | "member"; id: number }[];
};

export const getKnowledge = (roomId: number): Promise<KnowledgeData> =>
  req(`/api/rooms/${roomId}/knowledge`);

export type PlaceFields = {
  name: string;
  aliases?: string[];
  tags?: string[];
  delivery?: string[];
  address?: string | null;
  phone?: string | null;
  walkable?: boolean;
  walk_minutes?: number | null;
  price_hint?: number | null;
  closed_until?: string | null;
};

export const createPlace = (roomId: number, body: PlaceFields) =>
  req(`/api/rooms/${roomId}/places`, { method: "POST", body: JSON.stringify(body) });

/** Partial edit: omitted keys are left alone, an explicit `null` clears. `slug` is
 * absent by design — renaming a place must not detach its notes. */
export const patchPlace = (
  roomId: number,
  placeId: number,
  patch: Partial<PlaceFields> & { active?: boolean },
) =>
  req(`/api/rooms/${roomId}/places/${placeId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export type SlugRename = {
  ok: true;
  changed: boolean;
  slug: string;
  former_slug: string | null;
  notes_moved: number;
  notes_deduped: number;
  memos_moved: number;
};

/** Change a place's identity, moving its notes and pending memo cards with it.
 *
 * Deliberately not part of `patchPlace`: it is a migration across three stores,
 * and a form round-trip must never be able to trigger one. The slug is sent as
 * typed — the server normalises it through `places.slugify`, and reimplementing
 * that here would mean a second Vietnamese folder in TypeScript (`đ→d` is
 * hand-mapped, NFD leaves it whole) that could disagree with the real one.
 */
export const renamePlaceSlug = (
  roomId: number, placeId: number, slug: string,
): Promise<SlugRename> =>
  req(`/api/rooms/${roomId}/places/${placeId}/slug`, {
    method: "POST",
    body: JSON.stringify({ slug }),
  });

/** Hides the place (`active=false`). Meals still reference it, so it is never a row delete. */
export const deletePlace = (roomId: number, placeId: number) =>
  req(`/api/rooms/${roomId}/places/${placeId}`, { method: "DELETE" });

export type NoteFields = {
  text: string;
  standing?: boolean;
  when?: string | null;
  gate_kind?: GateKind | null;
  gate_at?: string | null;
};

export const createNote = (roomId: number, body: NoteFields & { subject: string }) =>
  req(`/api/rooms/${roomId}/observations`, { method: "POST", body: JSON.stringify(body) });

export const patchNote = (
  roomId: number,
  id: string,
  patch: Partial<NoteFields> & { etag: string },
) =>
  req(`/api/rooms/${roomId}/observations/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const deleteNote = (roomId: number, id: string, etag: string) =>
  req(`/api/rooms/${roomId}/observations/${id}?etag=${encodeURIComponent(etag)}`, {
    method: "DELETE",
  });

export const patchMemorySection = (roomId: number, index: number, etag: string, body: string) =>
  req(`/api/rooms/${roomId}/memory/sections/${index}`, {
    method: "PATCH",
    body: JSON.stringify({ etag, body }),
  });

export const deleteMemorySection = (roomId: number, index: number, etag: string) =>
  req(`/api/rooms/${roomId}/memory/sections/${index}?etag=${encodeURIComponent(etag)}`, {
    method: "DELETE",
  });

/** ⑦ one-tap "Đã trả": records the caller's outstanding for one meal (server
 * computes the amount from {to, meal_id}; client sends no money value). */
export const quickPay = (roomId: number, to: number, mealId: number): Promise<{ ok: boolean; payment_id: number; amount: number }> =>
  req(`/api/rooms/${roomId}/payments/quick`, { method: "POST", body: JSON.stringify({ to, meal_id: mealId }) });

/** Ask the bot to post the VietQR card covering everything the caller owes
 * `to` — deterministic server-side computation, no LLM turn. */
export const requestQr = (roomId: number, to: number): Promise<{ ok: boolean; amount: number }> =>
  req(`/api/rooms/${roomId}/qr-requests`, { method: "POST", body: JSON.stringify({ to }) });

export const getInvite = (roomId: number): Promise<{ invite_token: string }> =>
  req(`/api/rooms/${roomId}/invite`);

export const patchDraft = (roomId: number, draftId: number, patch: any) =>
  req(`/api/rooms/${roomId}/drafts/${draftId}`, { method: "PATCH", body: JSON.stringify(patch) });

export const commitDraft = (roomId: number, draftId: number) =>
  req(`/api/rooms/${roomId}/drafts/${draftId}/commit`, { method: "POST" });

export const recommitDraft = (
  roomId: number,
  draftId: number,
  fields: {
    payer_member_id: number;
    member_participants: number[];
    guests: string[];
    bill_total: number;
    adjustments: { member: number; amount: number }[];
    items?: { member: number; amount: number; label?: string | null }[];
    dish?: string | null;
    initiator?: string | null;
    note?: string | null;
  },
) =>
  req(`/api/rooms/${roomId}/drafts/${draftId}/recommit`, {
    method: "POST",
    body: JSON.stringify(fields),
  });

export const commitMemo = (roomId: number, memoId: number) =>
  req(`/api/rooms/${roomId}/memos/${memoId}/commit`, { method: "POST" });

export const cancelMemo = (roomId: number, memoId: number) =>
  req(`/api/rooms/${roomId}/memos/${memoId}/cancel`, { method: "POST" });

export const cancelDraft = (roomId: number, draftId: number) =>
  req(`/api/rooms/${roomId}/drafts/${draftId}`, {
    method: "PATCH",
    body: JSON.stringify({ status: "cancelled" }),
  });

/** Fetch a bounded window of messages for lazy scrollback.
 *  - `{ days }` — initial load: only the last N days (small recent slice).
 *  - `{ beforeId }` — load earlier: the page of older messages before an id.
 * Both return `{ messages, has_more }`; `has_more` drives the "load earlier" UI. */
export const getMessages = (
  roomId: number,
  opts: { days?: number; beforeId?: number } = {},
): Promise<{ messages: any[]; has_more?: boolean }> => {
  const p = new URLSearchParams();
  if (opts.days != null) p.set("days", String(opts.days));
  if (opts.beforeId != null) p.set("before_id", String(opts.beforeId));
  const qs = p.toString();
  return req(`/api/rooms/${roomId}/messages${qs ? `?${qs}` : ""}`);
};

export const postMessage = (roomId: number, body: string, images?: any[]) =>
  req(`/api/rooms/${roomId}/messages`, {
    method: "POST",
    body: JSON.stringify({ body, images }),
  });

export async function streamRoom(
  roomId: number,
  since: number,
  onEvent: (e: any) => void,
  signal: AbortSignal,
): Promise<void> {
  const tok = getToken();
  if (!tok) {
    // Never send "Authorization: Bearer null" — fail fast instead of
    // issuing an unauthenticated fetch, so the caller's reconnect logic
    // (which reacts to ApiError) has something to catch.
    throw new ApiError(401, "no session token");
  }
  const res = await fetch(`/api/rooms/${roomId}/stream?since=${since}`, {
    headers: { Authorization: `Bearer ${tok}` },
    signal,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}) as any);
    throw new ApiError(res.status, body.detail || res.statusText);
  }
  const reader = res.body!.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const { events, rest } = parseSSE(buf);
    buf = rest;
    events.forEach(onEvent);
  }
}
