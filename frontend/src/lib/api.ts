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
export const botHandle = async (): Promise<string> => "bot";

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
    dish?: string | null;
    initiator?: string | null;
    note?: string | null;
  },
) =>
  req(`/api/rooms/${roomId}/drafts/${draftId}/recommit`, {
    method: "POST",
    body: JSON.stringify(fields),
  });

export const cancelDraft = (roomId: number, draftId: number) =>
  req(`/api/rooms/${roomId}/drafts/${draftId}`, {
    method: "PATCH",
    body: JSON.stringify({ status: "cancelled" }),
  });

export const getMessages = (roomId: number, since = 0) =>
  req(`/api/rooms/${roomId}/messages?since=${since}`);

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
