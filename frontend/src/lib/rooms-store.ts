/** Multi-room localStorage store — the client's "who am I, where" state.
 *
 * There is no server-side cross-room identity (design 2026-07-22): each room
 * membership is an independent (member, token) pair. This module owns the
 * storage schema for that:
 *  - `chiatienan.rooms`   — [{roomId, roomName, token, lastAccessAt}]
 *  - `chiatienan.profile` — last-used profile fields, prefill-only
 * The active room is simply the entry with the newest lastAccessAt.
 */

export type StoredRoom = {
  roomId: number;
  roomName: string;
  token: string;
  lastAccessAt: number; // epoch ms
};

export type SavedProfile = {
  display_name?: string;
  nickname?: string;
  pin?: string;
  bank_code?: string;
  account_number?: string;
  account_holder?: string;
};

const ROOMS = "chiatienan.rooms";
const PROFILE = "chiatienan.profile";
const LEGACY_TOKEN = "chiatienan.token";
const LEGACY_ROOM = "chiatienan.room_id";

const hasStorage = () => typeof localStorage !== "undefined";

function readRooms(): StoredRoom[] {
  if (!hasStorage()) return [];
  try {
    const raw = JSON.parse(localStorage.getItem(ROOMS) || "[]");
    if (!Array.isArray(raw)) return [];
    return raw.filter(
      (r: any) => r && typeof r.roomId === "number" && typeof r.token === "string" && r.token,
    );
  } catch {
    return [];
  }
}

function writeRooms(rooms: StoredRoom[]): void {
  if (!hasStorage()) return;
  localStorage.setItem(ROOMS, JSON.stringify(rooms));
}

/** Move the pre-multi-room single token/room pair into the rooms list.
 * Idempotent; never overwrites an entry that already exists for the room. */
export function migrateLegacy(): void {
  if (!hasStorage()) return;
  const token = localStorage.getItem(LEGACY_TOKEN);
  const roomId = Number(localStorage.getItem(LEGACY_ROOM) || 0) || null;
  if (token && roomId && !readRooms().some((r) => r.roomId === roomId)) {
    // roomName is unknown here; the session provider resolves it lazily.
    writeRooms([...readRooms(), { roomId, roomName: "", token, lastAccessAt: Date.now() }]);
  }
  localStorage.removeItem(LEGACY_TOKEN);
  localStorage.removeItem(LEGACY_ROOM);
}

/** All rooms, most recently accessed first. */
export function listRooms(): StoredRoom[] {
  return readRooms().sort((a, b) => b.lastAccessAt - a.lastAccessAt);
}

export function activeRoom(): StoredRoom | null {
  return listRooms()[0] ?? null;
}

export function upsertRoom(r: { roomId: number; roomName: string; token: string }): void {
  const rest = readRooms().filter((x) => x.roomId !== r.roomId);
  writeRooms([...rest, { ...r, lastAccessAt: Date.now() }]);
}

export function touchRoom(roomId: number): void {
  writeRooms(
    readRooms().map((r) => (r.roomId === roomId ? { ...r, lastAccessAt: Date.now() } : r)),
  );
}

/** Set a room's display name without bumping its last-access time. */
export function renameRoom(roomId: number, roomName: string): void {
  writeRooms(readRooms().map((r) => (r.roomId === roomId ? { ...r, roomName } : r)));
}

export function removeRoom(roomId: number): void {
  writeRooms(readRooms().filter((r) => r.roomId !== roomId));
}

export function getProfile(): SavedProfile {
  if (!hasStorage()) return {};
  try {
    const raw = JSON.parse(localStorage.getItem(PROFILE) || "{}");
    return raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
  } catch {
    return {};
  }
}

/** Merge defined fields into the saved profile (save-back on edits). */
export function saveProfile(p: SavedProfile): void {
  if (!hasStorage()) return;
  const merged: Record<string, string> = { ...(getProfile() as Record<string, string>) };
  for (const [k, v] of Object.entries(p)) {
    if (v !== undefined) merged[k] = v;
  }
  localStorage.setItem(PROFILE, JSON.stringify(merged));
}
