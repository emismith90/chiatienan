# Multi-Room Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A person can create and join multiple rooms and switch between them, with no server-side cross-room identity — the client holds a `room_id → token` map and a saved profile.

**Architecture:** Backend gains exactly one public endpoint (`POST /api/rooms/create` = room + first member + session in one call); everything else stays untouched — isolation via `_check_room` and room-scoped queries is preserved. The frontend replaces its single token/room localStorage pair with a rooms list (`chiatienan.rooms`) whose active room is the most recently accessed, plus a saved profile (`chiatienan.profile`) that prefills join/create forms. A header room-menu hosts switch/create/remove.

**Tech Stack:** FastAPI + SQLAlchemy + pytest (backend); Next.js 16 + React 19 + vitest + testing-library (frontend).

**Spec:** `docs/superpowers/specs/2026-07-22-multi-room-support-design.md`

## Global Constraints

- **No backend schema changes.** No new tables/columns; no migration framework exists.
- **Isolation invariant:** every authed room route must keep 403-ing when the token's room ≠ path room (`_check_room`, `backend/app/main.py:176`).
- **localStorage keys:** `chiatienan.rooms` (JSON array of `{roomId, roomName, token, lastAccessAt}`), `chiatienan.profile` (JSON object with backend field names: `display_name, nickname, pin, bank_code, account_number, account_holder`). Legacy keys `chiatienan.token` / `chiatienan.room_id` are migrated then removed.
- **Active room = newest `lastAccessAt`.** No URL-per-room routing.
- **No profile fan-out:** profile edits save back to `chiatienan.profile` only; other rooms' server members are never updated.
- Storing the PIN in localStorage is accepted — the codebase treats PIN as "identity handle, not a secret" (`backend/app/models.py`).
- Backend tests: `cd backend && .venv/bin/python -m pytest tests/... -v`. Frontend tests: `cd frontend && npx vitest run <path>`.
- All frontend UI copy in English, styled with the existing CSS-variable classes (copy `inputClass` etc. from neighboring files).
- Match existing SSR guard idiom: never touch `localStorage` outside `typeof localStorage !== "undefined"` guards or client-side effects.

---

### Task 1: Backend — public `POST /api/rooms/create`

**Files:**
- Modify: `backend/app/main.py` (add model near line 40, route after the admin `create_room` at line 96-103)
- Test: `backend/tests/test_api.py` (append)

**Interfaces:**
- Consumes: `rooms.create_room(s, name)` (`backend/app/rooms.py:8`), `accounts.create_account(s, room, *, display_name, nickname, pin, bank_code, account_number, account_holder)` (`backend/app/accounts.py:37`) — both already exist.
- Produces: `POST /api/rooms/create` — request `{room_name, display_name, nickname, pin, bank_code?, account_number?, account_holder?}`, response `{token, room_id, room_name, member_id, invite_token}`. 422 on missing nickname/PIN. Task 3's `api.createRoom` calls this.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_api.py`:

```python
def test_public_create_room_creates_room_member_session(client):
    r = client.post("/api/rooms/create", json={
        "room_name": "Team A", "display_name": "An", "nickname": "an", "pin": "1234",
        "bank_code": "VCB", "account_number": "007", "account_holder": "AN NGUYEN",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["room_name"] == "Team A"
    assert body["invite_token"]
    # The returned token is a working session for the new room.
    h = {"Authorization": f"Bearer {body['token']}"}
    members = client.get(f"/api/rooms/{body['room_id']}/members", headers=h)
    assert members.status_code == 200
    assert members.json()[0]["nickname"] == "an"
    # The invite token admits a second joiner into the same room.
    _sess_b, rid = _join(client, body["invite_token"], "binh")
    assert rid == body["room_id"]


def test_public_create_room_rejects_missing_nickname_or_pin(client):
    r = client.post("/api/rooms/create", json={
        "room_name": "X", "display_name": "A", "nickname": "", "pin": ""})
    assert r.status_code == 422


def test_public_create_room_is_isolated_from_other_rooms(client):
    a = client.post("/api/rooms/create", json={
        "room_name": "A", "display_name": "An", "nickname": "an", "pin": "1"}).json()
    b = client.post("/api/rooms/create", json={
        "room_name": "B", "display_name": "Binh", "nickname": "binh", "pin": "2"}).json()
    ha = {"Authorization": f"Bearer {a['token']}"}
    assert client.get(f"/api/rooms/{b['room_id']}/messages", headers=ha).status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api.py -k public_create_room -v`
Expected: 3 FAILED — each with `assert 404 == 200` style errors (route doesn't exist yet; FastAPI matches no POST route → 404 or 405).

- [ ] **Step 3: Implement the endpoint**

In `backend/app/main.py`, add the request model after `RoomIn` (line 38-39):

```python
class RoomCreateIn(BaseModel):
    room_name: str = "Lunch"
    display_name: str
    nickname: str
    pin: str
    bank_code: str | None = None
    account_number: str | None = None
    account_holder: str | None = None
```

Add the route directly after the admin `create_room` route (after line 103). Note: it must be a **static path** (`/api/rooms/create`) and is only registered for POST, so it cannot shadow `GET /api/rooms/{invite_token}`:

```python
@app.post("/api/rooms/create")
async def create_room_public(body: RoomCreateIn):
    """Anyone can start a room: creates the room + its first member + a session.

    Public by design (multi-room spec 2026-07-22) — the invite-link join flow
    is the same trust model, and rooms are cheap. Member-field validation
    (nickname/PIN required, etc.) lives in accounts.create_account.
    """
    with get_db().session() as s:
        r = rooms.create_room(s, body.room_name)
        try:
            m, tok = accounts.create_account(
                s, r,
                display_name=body.display_name, nickname=body.nickname, pin=body.pin,
                bank_code=body.bank_code, account_number=body.account_number,
                account_holder=body.account_holder,
            )
        except accounts.AccountError as e:
            raise HTTPException(422, str(e))
        return {
            "token": tok, "room_id": r.id, "room_name": r.name,
            "member_id": m.id, "invite_token": r.invite_token,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api.py -v`
Expected: all PASS (the 3 new tests plus every pre-existing test in the file).

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_api.py
git commit -m "feat(be): public POST /api/rooms/create — room + first member + session"
```

---

### Task 2: Frontend — `rooms-store.ts` (multi-room storage + saved profile)

**Files:**
- Create: `frontend/src/lib/rooms-store.ts`
- Test: `frontend/src/lib/__tests__/rooms-store.test.ts`

**Interfaces:**
- Consumes: nothing (pure localStorage module).
- Produces (used by Tasks 3-8):
  - `type StoredRoom = { roomId: number; roomName: string; token: string; lastAccessAt: number }`
  - `type SavedProfile = { display_name?: string; nickname?: string; pin?: string; bank_code?: string; account_number?: string; account_holder?: string }`
  - `migrateLegacy(): void` — moves legacy `chiatienan.token`/`chiatienan.room_id` into the list, removes legacy keys
  - `listRooms(): StoredRoom[]` — newest `lastAccessAt` first
  - `activeRoom(): StoredRoom | null`
  - `upsertRoom(r: {roomId: number; roomName: string; token: string}): void` — bumps `lastAccessAt`
  - `touchRoom(roomId: number): void` — bumps `lastAccessAt`
  - `renameRoom(roomId: number, roomName: string): void` — does NOT bump
  - `removeRoom(roomId: number): void`
  - `getProfile(): SavedProfile` / `saveProfile(p: SavedProfile): void` (merge of defined keys)

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/__tests__/rooms-store.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  activeRoom, getProfile, listRooms, migrateLegacy, removeRoom,
  renameRoom, saveProfile, touchRoom, upsertRoom,
} from "../rooms-store";

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-07-22T12:00:00Z"));
});
afterEach(() => vi.useRealTimers());

const tick = () => vi.advanceTimersByTime(1000);

describe("rooms list", () => {
  it("starts empty", () => {
    expect(listRooms()).toEqual([]);
    expect(activeRoom()).toBeNull();
  });

  it("upsert adds a room and makes it active; newest access wins", () => {
    upsertRoom({ roomId: 1, roomName: "A", token: "ta" });
    tick();
    upsertRoom({ roomId: 2, roomName: "B", token: "tb" });
    expect(activeRoom()?.roomId).toBe(2);
    tick();
    touchRoom(1);
    expect(activeRoom()?.roomId).toBe(1);
    expect(listRooms().map((r) => r.roomId)).toEqual([1, 2]);
  });

  it("upsert replaces an existing entry (rejoin refreshes the token)", () => {
    upsertRoom({ roomId: 1, roomName: "A", token: "old" });
    tick();
    upsertRoom({ roomId: 1, roomName: "A2", token: "new" });
    expect(listRooms()).toHaveLength(1);
    expect(activeRoom()).toMatchObject({ token: "new", roomName: "A2" });
  });

  it("renameRoom sets the name without bumping last access", () => {
    upsertRoom({ roomId: 1, roomName: "", token: "ta" });
    tick();
    upsertRoom({ roomId: 2, roomName: "B", token: "tb" });
    tick();
    renameRoom(1, "Named");
    expect(activeRoom()?.roomId).toBe(2); // rename must not steal the pointer
    expect(listRooms().find((r) => r.roomId === 1)?.roomName).toBe("Named");
  });

  it("removeRoom evicts; the next most-recent becomes active", () => {
    upsertRoom({ roomId: 1, roomName: "A", token: "ta" });
    tick();
    upsertRoom({ roomId: 2, roomName: "B", token: "tb" });
    removeRoom(2);
    expect(activeRoom()?.roomId).toBe(1);
    removeRoom(1);
    expect(activeRoom()).toBeNull();
  });

  it("survives corrupt storage", () => {
    localStorage.setItem("chiatienan.rooms", "not json");
    expect(listRooms()).toEqual([]);
    localStorage.setItem("chiatienan.rooms", JSON.stringify([{ bogus: true }]));
    expect(listRooms()).toEqual([]);
  });
});

describe("legacy migration", () => {
  it("moves the single token/room pair into the list and removes legacy keys", () => {
    localStorage.setItem("chiatienan.token", "legacy-tok");
    localStorage.setItem("chiatienan.room_id", "7");
    migrateLegacy();
    expect(activeRoom()).toMatchObject({ roomId: 7, token: "legacy-tok", roomName: "" });
    expect(localStorage.getItem("chiatienan.token")).toBeNull();
    expect(localStorage.getItem("chiatienan.room_id")).toBeNull();
  });

  it("is a no-op without legacy keys and never duplicates an existing entry", () => {
    upsertRoom({ roomId: 7, roomName: "A", token: "current" });
    localStorage.setItem("chiatienan.token", "stale");
    localStorage.setItem("chiatienan.room_id", "7");
    migrateLegacy();
    expect(listRooms()).toHaveLength(1);
    expect(activeRoom()?.token).toBe("current");
    migrateLegacy(); // idempotent
    expect(listRooms()).toHaveLength(1);
  });
});

describe("saved profile", () => {
  it("round-trips and merges defined keys only", () => {
    expect(getProfile()).toEqual({});
    saveProfile({ nickname: "an", pin: "1234", display_name: "An" });
    saveProfile({ bank_code: "VCB", display_name: undefined });
    expect(getProfile()).toEqual({
      nickname: "an", pin: "1234", display_name: "An", bank_code: "VCB",
    });
  });

  it("survives corrupt storage", () => {
    localStorage.setItem("chiatienan.profile", "{{{");
    expect(getProfile()).toEqual({});
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/__tests__/rooms-store.test.ts`
Expected: FAIL — `Cannot find module '../rooms-store'` (or equivalent resolve error).

- [ ] **Step 3: Implement the module**

Create `frontend/src/lib/rooms-store.ts`:

```ts
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/lib/__tests__/rooms-store.test.ts`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/rooms-store.ts frontend/src/lib/__tests__/rooms-store.test.ts
git commit -m "feat(fe): rooms-store — multi-room localStorage list + saved profile"
```

---

### Task 3: Frontend — `api.ts` delegates tokens to rooms-store; add `createRoom`

**Files:**
- Modify: `frontend/src/lib/api.ts:1-21` (token helpers), append `createRoom`
- Modify: `frontend/src/lib/__tests__/api.test.ts` (seed via rooms-store; drop legacy helper tests)

**Interfaces:**
- Consumes: `activeRoom()` from Task 2.
- Produces:
  - `getToken(): string | null` — now `activeRoom()?.token ?? null` (same name/signature as before; `req` and `streamRoom` keep using it unchanged)
  - `createRoom(b: {room_name: string; display_name: string; nickname: string; pin: string; bank_code?: string; account_number?: string; account_holder?: string}): Promise<{token: string; room_id: number; room_name: string; member_id: number; invite_token: string}>`
  - **Removed:** `setToken`, `getRoomId`, `setRoomId`, `clearSession` (their only consumer, `session.tsx`, is rewritten in Task 4 — this task and Task 4 must land together for the build to stay green; run `npx tsc --noEmit` only at the end of Task 4).

- [ ] **Step 1: Update the tests first**

In `frontend/src/lib/__tests__/api.test.ts`:

1. Replace the imports/setup at the top:

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import * as api from "../api";
import { upsertRoom } from "../rooms-store";

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

/** Seed a stored room so getToken() (which reads the active room) returns t. */
const seedToken = (t: string) => upsertRoom({ roomId: 1, roomName: "R", token: t });
```

2. Replace every `api.setToken("t123")` call with `seedToken("t123")` (occurrences at the current lines 10, 39, 67, 93, 106).
3. Delete the whole `describe("session storage helpers", ...)` block (current lines 21-35) — rooms-store.test.ts covers storage now.
4. Append a new block:

```ts
describe("createRoom", () => {
  it("POSTs to /api/rooms/create without auth", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ token: "t", room_id: 5, room_name: "A", member_id: 1, invite_token: "iv" }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const res = await api.createRoom({
      room_name: "A", display_name: "An", nickname: "an", pin: "1234",
    });
    expect(res.room_id).toBe(5);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/rooms/create");
    expect(init.method).toBe("POST");
    expect((init.headers as any).Authorization).toBeUndefined();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/__tests__/api.test.ts`
Expected: FAIL — `api.createRoom is not a function`, and the bearer-token test fails because `getToken` still reads the legacy key (`Authorization` is `undefined`).

- [ ] **Step 3: Implement**

In `frontend/src/lib/api.ts`, replace lines 1-21 with:

```ts
import { parseSSE } from "./sse";
import { activeRoom } from "./rooms-store";

/** Token for the active (most recently accessed) room — see rooms-store. */
export const getToken = (): string | null => activeRoom()?.token ?? null;
```

Then append next to the other account calls (after `identify`, current line 58-59):

```ts
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/lib/__tests__/api.test.ts src/lib/__tests__/rooms-store.test.ts`
Expected: all PASS. (Do NOT run `tsc` yet — `session.tsx` still imports the removed helpers until Task 4.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/__tests__/api.test.ts
git commit -m "feat(fe): api tokens from rooms-store active room; add createRoom client"
```

---

### Task 4: Frontend — session provider rework (rooms, switch, evict)

**Files:**
- Modify: `frontend/src/lib/session.tsx` (full rewrite, shown below)
- Modify: `frontend/src/app/join/[token]/page.tsx:86` (pass room name to `signIn` — minimal edit; the rest of the join page changes come in Task 7)
- Test: `frontend/src/lib/__tests__/session.test.tsx` (new)

**Interfaces:**
- Consumes: Task 2 store functions; `getMe`, `getInvite`, `roomInfo` from `api.ts`.
- Produces — the new `useSession()` context shape (Tasks 5-8 rely on these exact names):

```ts
type Ctx = {
  token: string | null;        // active room's token (null when no rooms)
  roomId: number | null;       // active room's id
  roomName: string;            // active room's name ("" while unresolved)
  rooms: StoredRoom[];         // all rooms, most recent first
  ready: boolean;
  memberId: number | null;     // your member id in the ACTIVE room
  signIn: (token: string, roomId: number, roomName: string) => void; // upsert + make active
  signOut: () => void;         // evict the ACTIVE room; falls back to next
  switchRoom: (roomId: number) => void;
  removeRoom: (roomId: number) => void;
};
```

- Existing consumers stay compatible: `page.tsx` uses `token/roomId/ready`; `use-room.ts` uses `signOut/memberId` — `signOut` now evicts only the active room, which **is** the spec's 401-eviction-with-fallback behavior (`use-room.ts:168,210` call it on 401).

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/__tests__/session.test.tsx`:

```tsx
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { SessionProvider, useSession } from "../session";
import { listRooms, upsertRoom } from "../rooms-store";

vi.mock("../api", () => ({
  getMe: vi.fn().mockResolvedValue({ id: 7 }),
  getInvite: vi.fn().mockRejectedValue(new Error("offline")),
  roomInfo: vi.fn(),
}));

function Probe() {
  const s = useSession();
  if (!s.ready) return null;
  return (
    <div>
      <span data-testid="room">{s.roomId ?? "none"}</span>
      <span data-testid="name">{s.roomName}</span>
      <span data-testid="count">{s.rooms.length}</span>
      <button onClick={() => s.signIn("t3", 3, "C")}>in</button>
      <button onClick={() => s.switchRoom(1)}>sw</button>
      <button onClick={() => s.signOut()}>out</button>
    </div>
  );
}

const setup = () => render(<SessionProvider><Probe /></SessionProvider>);

beforeEach(() => localStorage.clear());

describe("SessionProvider", () => {
  it("shows no room when storage is empty", async () => {
    setup();
    expect(await screen.findByTestId("room")).toHaveTextContent("none");
  });

  it("migrates legacy keys on mount", async () => {
    localStorage.setItem("chiatienan.token", "legacy");
    localStorage.setItem("chiatienan.room_id", "9");
    setup();
    expect(await screen.findByTestId("room")).toHaveTextContent("9");
    expect(localStorage.getItem("chiatienan.token")).toBeNull();
  });

  it("signIn adds a room and makes it active; switchRoom moves the pointer; signOut evicts with fallback", async () => {
    upsertRoom({ roomId: 1, roomName: "A", token: "t1" });
    setup();
    expect(await screen.findByTestId("room")).toHaveTextContent("1");

    act(() => screen.getByText("in").click());
    expect(screen.getByTestId("room")).toHaveTextContent("3");
    expect(screen.getByTestId("name")).toHaveTextContent("C");
    expect(screen.getByTestId("count")).toHaveTextContent("2");

    act(() => screen.getByText("sw").click());
    expect(screen.getByTestId("room")).toHaveTextContent("1");

    act(() => screen.getByText("out").click()); // evicts room 1 → falls back to 3
    expect(screen.getByTestId("room")).toHaveTextContent("3");
    expect(listRooms().map((r) => r.roomId)).toEqual([3]);

    act(() => screen.getByText("out").click()); // last room gone → none
    expect(screen.getByTestId("room")).toHaveTextContent("none");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/__tests__/session.test.tsx`
Expected: FAIL — the provider still exposes the old `signIn(t, r)` shape / reads legacy keys (errors like `s.switchRoom is not a function`).

- [ ] **Step 3: Rewrite the provider**

Replace `frontend/src/lib/session.tsx` entirely with:

```tsx
"use client";
import { createContext, useContext, useEffect, useState } from "react";
import { getInvite, getMe, roomInfo } from "./api";
import {
  StoredRoom, listRooms, migrateLegacy, renameRoom,
  removeRoom as storeRemoveRoom, touchRoom, upsertRoom,
} from "./rooms-store";

type Ctx = {
  token: string | null; roomId: number | null; roomName: string;
  rooms: StoredRoom[]; ready: boolean; memberId: number | null;
  signIn: (token: string, roomId: number, roomName: string) => void;
  signOut: () => void;
  switchRoom: (roomId: number) => void;
  removeRoom: (roomId: number) => void;
};
const SessionCtx = createContext<Ctx>(null as any);
export const useSession = () => useContext(SessionCtx);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [rooms, setRooms] = useState<StoredRoom[]>([]);
  const [ready, setReady] = useState(false);
  const [memberId, setMemberId] = useState<number | null>(null);

  const refresh = () => setRooms(listRooms());

  useEffect(() => { migrateLegacy(); refresh(); setReady(true); }, []);

  const active = rooms[0] ?? null;
  const token = active?.token ?? null;
  const roomId = active?.roomId ?? null;

  // Legacy-migrated entries carry no name — resolve the active one lazily:
  // invite token (authed, active room) → public room info → name.
  useEffect(() => {
    if (!active || active.roomName) return;
    let live = true;
    getInvite(active.roomId)
      .then(({ invite_token }) => roomInfo(invite_token))
      .then((info: any) => {
        if (!live || !info?.name) return;
        renameRoom(active.roomId, info.name);
        refresh();
      })
      .catch(() => {});
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active?.roomId, active?.roomName]);

  // Fetch the logged-in member's own id so callers (e.g. the optimistic
  // send echo in useRoom) can stamp outgoing messages with the real author.
  useEffect(() => {
    if (!token) { setMemberId(null); return; }
    let live = true;
    getMe().then((me: any) => { if (live) setMemberId(me?.id ?? null); }).catch(() => {});
    return () => { live = false; };
  }, [token]);

  const signIn = (t: string, r: number, name: string) => {
    upsertRoom({ roomId: r, roomName: name, token: t });
    refresh();
  };
  const switchRoom = (r: number) => { touchRoom(r); refresh(); };
  const removeRoom = (r: number) => { storeRemoveRoom(r); refresh(); };
  // "Sign out" of the active room = drop it from this device; the next
  // most-recent room (if any) takes over. Server-side member is untouched.
  const signOut = () => {
    if (active) storeRemoveRoom(active.roomId);
    setMemberId(null);
    refresh();
  };

  return (
    <SessionCtx.Provider
      value={{ token, roomId, roomName: active?.roomName ?? "", rooms, ready,
               memberId, signIn, signOut, switchRoom, removeRoom }}
    >
      {children}
    </SessionCtx.Provider>
  );
}
```

- [ ] **Step 4: Update the one existing `signIn` caller**

In `frontend/src/app/join/[token]/page.tsx`, change line 86:

```ts
      signIn(res.token, res.room_id);
```

to:

```ts
      signIn(res.token, res.room_id, room?.name ?? "");
```

(`room` is the state already loaded from `api.roomInfo` at line 56-61; it is always set by submit time since the form only renders once `room` is non-null.)

- [ ] **Step 5: Run tests and the type check**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: all vitest suites PASS; tsc clean (the removed api helpers have no remaining importers).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/session.tsx frontend/src/lib/__tests__/session.test.tsx "frontend/src/app/join/[token]/page.tsx"
git commit -m "feat(fe): session provider — rooms list, switchRoom, evict-with-fallback"
```

---

### Task 5: Frontend — RoomSwitcher header menu

**Files:**
- Create: `frontend/src/components/chat/room-switcher.tsx`
- Modify: `frontend/src/components/chat/room-view.tsx:377-391` (replace the static `<h1>chiatienan</h1>` block)
- Test: `frontend/src/components/chat/__tests__/room-switcher.test.tsx`

**Interfaces:**
- Consumes: `useSession()` (`rooms`, `roomId`, `roomName`, `switchRoom`, `removeRoom`) from Task 4; `useRouter` from `next/navigation`.
- Produces: `export function RoomSwitcher(): JSX.Element` — no props; self-contained via session context.

Per the spec (amended): the menu **button always renders** showing the active room's name (fallback "chiatienan" while unresolved); the switch list inside the menu appears only when there is more than one room; the menu always offers "Create a room" and "Remove from this device".

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/chat/__tests__/room-switcher.test.tsx`:

```tsx
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { RoomSwitcher } from "../room-switcher";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const session = {
  rooms: [] as any[], roomId: 1 as number | null, roomName: "Lunch A",
  switchRoom: vi.fn(), removeRoom: vi.fn(),
};
vi.mock("@/lib/session", () => ({ useSession: () => session }));

const oneRoom = [{ roomId: 1, roomName: "Lunch A", token: "t1", lastAccessAt: 2 }];
const twoRooms = [
  ...oneRoom,
  { roomId: 2, roomName: "Lunch B", token: "t2", lastAccessAt: 1 },
];

beforeEach(() => {
  vi.clearAllMocks();
  session.rooms = oneRoom;
  session.roomId = 1;
  session.roomName = "Lunch A";
});

describe("RoomSwitcher", () => {
  it("shows the active room name and opens the menu", () => {
    render(<RoomSwitcher />);
    const btn = screen.getByRole("button", { name: /room menu/i });
    expect(btn).toHaveTextContent("Lunch A");
    fireEvent.click(btn);
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("hides the switch list with a single room but still offers create/remove", () => {
    render(<RoomSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: /room menu/i }));
    expect(screen.queryByText("Switch room")).not.toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /create a room/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /remove from this device/i })).toBeInTheDocument();
  });

  it("lists other rooms when there are several and switches on tap", () => {
    session.rooms = twoRooms;
    render(<RoomSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: /room menu/i }));
    expect(screen.getByText("Switch room")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: "Lunch B" }));
    expect(session.switchRoom).toHaveBeenCalledWith(2);
  });

  it("navigates to /create from the menu", () => {
    render(<RoomSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: /room menu/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /create a room/i }));
    expect(push).toHaveBeenCalledWith("/create");
  });

  it("removes the current room only after confirmation", () => {
    render(<RoomSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: /room menu/i }));
    vi.spyOn(window, "confirm").mockReturnValueOnce(false);
    fireEvent.click(screen.getByRole("menuitem", { name: /remove from this device/i }));
    expect(session.removeRoom).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /room menu/i }));
    vi.spyOn(window, "confirm").mockReturnValueOnce(true);
    fireEvent.click(screen.getByRole("menuitem", { name: /remove from this device/i }));
    expect(session.removeRoom).toHaveBeenCalledWith(1);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/chat/__tests__/room-switcher.test.tsx`
Expected: FAIL — cannot resolve `../room-switcher`.

- [ ] **Step 3: Implement the component**

Create `frontend/src/components/chat/room-switcher.tsx`:

```tsx
"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "@/lib/session";

/** Header room-name button + dropdown menu (multi-room spec 2026-07-22).
 * Single-room users see just their room's name; the menu hosts switching
 * (only when >1 room), creating a room, and removing this room locally. */
export function RoomSwitcher() {
  const { rooms, roomId, roomName, switchRoom, removeRoom } = useSession();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const others = rooms.filter((r) => r.roomId !== roomId);
  const itemClass =
    "block w-full rounded px-3 py-2 text-left text-sm text-[var(--text-primary)] transition-colors duration-150 hover:bg-[var(--bg-base)]";

  return (
    <div ref={ref} className="relative min-w-0">
      <button
        type="button"
        aria-label="Room menu"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex min-w-0 items-center gap-1 text-base font-semibold text-[var(--text-primary)]"
      >
        <span className="truncate">{roomName || "chiatienan"}</span>
        <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden className="h-4 w-4 shrink-0 text-[var(--text-secondary)]">
          <path
            fillRule="evenodd"
            d="M5.22 8.22a.75.75 0 0 1 1.06 0L10 11.94l3.72-3.72a.75.75 0 1 1 1.06 1.06l-4.25 4.25a.75.75 0 0 1-1.06 0L5.22 9.28a.75.75 0 0 1 0-1.06Z"
            clipRule="evenodd"
          />
        </svg>
      </button>
      {open && (
        <div
          role="menu"
          className="absolute left-0 top-full z-50 mt-2 w-56 rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-1.5 shadow-xl"
        >
          {others.length > 0 && (
            <>
              <p className="px-3 pb-1 pt-1.5 text-xs font-medium uppercase tracking-wide text-[var(--text-secondary)]">
                Switch room
              </p>
              {others.map((r) => (
                <button
                  key={r.roomId}
                  type="button"
                  role="menuitem"
                  onClick={() => { switchRoom(r.roomId); setOpen(false); }}
                  className={itemClass}
                >
                  {r.roomName || `Room ${r.roomId}`}
                </button>
              ))}
              <hr className="my-1.5 border-[var(--border)]" />
            </>
          )}
          <button
            type="button"
            role="menuitem"
            onClick={() => { setOpen(false); router.push("/create"); }}
            className={itemClass}
          >
            Create a room
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false); // close first — declining the confirm shouldn't leave the menu up
              if (roomId != null &&
                  window.confirm("Remove this room from this device? Your account in the room is kept.")) {
                removeRoom(roomId);
              }
            }}
            className={`${itemClass} text-[var(--text-secondary)]`}
          >
            Remove from this device
          </button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Wire it into the header**

In `frontend/src/components/chat/room-view.tsx`, add the import at the top with the other component imports:

```ts
import { RoomSwitcher } from "./room-switcher";
```

Then replace the title block (lines 378-391, the `<div className="flex min-w-0 items-center gap-2">` containing the `<h1>` and the offline chip) with:

```tsx
            <div className="flex min-w-0 items-center gap-2">
              <RoomSwitcher />
              {!online && (
                <span
                  role="status"
                  className="inline-flex shrink-0 items-center gap-1 rounded-full border border-[var(--border)] bg-[var(--bg-base)] px-2 py-0.5 text-xs text-[var(--text-secondary)]"
                >
                  <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-[var(--text-secondary)]" />
                  Offline
                </span>
              )}
            </div>
```

(The offline chip markup is unchanged — only the `<h1>chiatienan</h1>` is replaced by `<RoomSwitcher />`.)

- [ ] **Step 5: Run tests and the type check**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: all PASS, tsc clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/chat/room-switcher.tsx frontend/src/components/chat/__tests__/room-switcher.test.tsx frontend/src/components/chat/room-view.tsx
git commit -m "feat(fe): room switcher menu in the chat header"
```

---

### Task 6: Frontend — `/create` page + landing-state link

**Files:**
- Create: `frontend/src/app/create/page.tsx`
- Modify: `frontend/src/app/page.tsx:10-21` (landing state gains a "Create a room" link)
- Test: `frontend/src/app/__tests__/create-page.test.tsx` (new directory)

**Interfaces:**
- Consumes: `api.createRoom` (Task 3), `getProfile`/`saveProfile` (Task 2), `useSession().signIn` (Task 4).
- Produces: route `/create`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/app/__tests__/create-page.test.tsx`:

```tsx
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import CreateRoom from "../create/page";
import { getProfile, saveProfile } from "@/lib/rooms-store";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const signIn = vi.fn();
vi.mock("@/lib/session", () => ({ useSession: () => ({ signIn }) }));

const createRoom = vi.fn();
vi.mock("@/lib/api", () => ({ createRoom: (...a: any[]) => createRoom(...a) }));

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});

describe("CreateRoom page", () => {
  it("prefills member fields from the saved profile", async () => {
    saveProfile({ nickname: "an", display_name: "An", pin: "1234", bank_code: "VCB" });
    render(<CreateRoom />);
    await waitFor(() =>
      expect(screen.getByLabelText("Nickname")).toHaveValue("an"));
    expect(screen.getByLabelText("Display name")).toHaveValue("An");
    expect(screen.getByLabelText("PIN")).toHaveValue("1234");
    expect(screen.getByLabelText("Bank code")).toHaveValue("VCB");
    expect(screen.getByLabelText("Room name")).toHaveValue("");
  });

  it("creates the room, saves the profile back, signs in, and goes home", async () => {
    createRoom.mockResolvedValue({
      token: "tok", room_id: 9, room_name: "Team", member_id: 1, invite_token: "iv",
    });
    render(<CreateRoom />);
    fireEvent.change(screen.getByLabelText("Room name"), { target: { value: "Team" } });
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "An" } });
    fireEvent.change(screen.getByLabelText("Nickname"), { target: { value: "an" } });
    fireEvent.change(screen.getByLabelText("PIN"), { target: { value: "1234" } });
    fireEvent.click(screen.getByRole("button", { name: /create room/i }));

    await waitFor(() => expect(signIn).toHaveBeenCalledWith("tok", 9, "Team"));
    expect(createRoom).toHaveBeenCalledWith(expect.objectContaining({
      room_name: "Team", nickname: "an", pin: "1234",
    }));
    expect(getProfile()).toMatchObject({ nickname: "an", pin: "1234", display_name: "An" });
    expect(push).toHaveBeenCalledWith("/");
  });

  it("shows the server error message on failure", async () => {
    createRoom.mockRejectedValue(new Error("Nickname and PIN are required."));
    render(<CreateRoom />);
    fireEvent.click(screen.getByRole("button", { name: /create room/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Nickname and PIN are required.");
    expect(signIn).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/app/__tests__/create-page.test.tsx`
Expected: FAIL — cannot resolve `../create/page`.

- [ ] **Step 3: Implement the page**

Create `frontend/src/app/create/page.tsx`:

```tsx
"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import * as api from "@/lib/api";
import { getProfile, saveProfile } from "@/lib/rooms-store";
import { useSession } from "@/lib/session";

const inputClass =
  "w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-2 text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] focus:outline-none focus-visible:ring-2 ring-[var(--accent-primary)] transition-all duration-150";

export default function CreateRoom() {
  const router = useRouter();
  const { signIn } = useSession();
  const [f, setF] = useState({
    room_name: "",
    display_name: "",
    nickname: "",
    pin: "",
    bank_code: "",
    account_number: "",
    account_holder: "",
  });
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  // Prefill member fields from the client-saved profile (design 2026-07-22) —
  // in an effect, not the initializer, so SSR prerender never touches storage.
  useEffect(() => {
    const p = getProfile();
    setF((prev) => ({
      ...prev,
      display_name: p.display_name ?? "",
      nickname: p.nickname ?? "",
      pin: p.pin ?? "",
      bank_code: p.bank_code ?? "",
      account_number: p.account_number ?? "",
      account_holder: p.account_holder ?? "",
    }));
  }, []);

  function set<K extends keyof typeof f>(key: K, value: string) {
    setF((prev) => ({ ...prev, [key]: value }));
  }

  async function submit() {
    setErr("");
    setLoading(true);
    try {
      const res = await api.createRoom(f);
      const { room_name: _room, ...profile } = f;
      saveProfile(profile);
      signIn(res.token, res.room_id, res.room_name);
      router.push("/");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Something went wrong, please try again.");
    } finally {
      setLoading(false);
    }
  }

  const fields: { key: keyof typeof f; label: string; placeholder: string; type?: string }[] = [
    { key: "room_name", label: "Room name", placeholder: "Room name (e.g. Lunch crew)" },
    { key: "display_name", label: "Display name", placeholder: "Display name" },
    { key: "nickname", label: "Nickname", placeholder: "Nickname" },
    { key: "pin", label: "PIN", placeholder: "PIN", type: "password" },
    { key: "bank_code", label: "Bank code", placeholder: "Bank code (e.g. VCB)" },
    { key: "account_number", label: "Account number", placeholder: "Account number" },
    { key: "account_holder", label: "Account holder", placeholder: "Account holder" },
  ];

  return (
    <main className="min-h-dvh pt-safe pb-safe bg-[var(--bg-base)] flex items-center justify-center p-4">
      <div className="bg-[var(--bg-surface)] rounded-lg border border-[var(--border)] shadow-md max-w-md w-full p-6 space-y-4">
        <div>
          <h1 className="text-lg font-semibold text-[var(--text-primary)]">Create a room</h1>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Start a new group and invite people with a link.
          </p>
        </div>

        <div className="space-y-3">
          {fields.map(({ key, label, placeholder, type }) => (
            <input
              key={key}
              aria-label={label}
              placeholder={placeholder}
              type={type ?? "text"}
              inputMode={key === "pin" ? "numeric" : undefined}
              value={f[key]}
              onChange={(e) => set(key, e.target.value)}
              className={inputClass}
            />
          ))}
        </div>

        {err && (
          <p role="alert" className="text-sm text-[var(--accent-text)]">
            {err}
          </p>
        )}

        <button
          type="button"
          onClick={submit}
          disabled={loading}
          className="w-full rounded-md bg-[var(--accent-primary)] hover:bg-[var(--accent-hover)] text-white py-2 transition-all duration-150 disabled:opacity-50"
        >
          {loading ? "Creating…" : "Create room"}
        </button>
      </div>
    </main>
  );
}
```

- [ ] **Step 4: Add the landing-state link**

In `frontend/src/app/page.tsx`, inside the no-room branch, after the `<p>…invite link…</p>` (line 15-17), add:

```tsx
          <a
            href="/create"
            className="mt-4 inline-block rounded-md bg-[var(--accent-primary)] px-4 py-2 text-sm text-white transition-all duration-150 hover:bg-[var(--accent-hover)]"
          >
            Create a room
          </a>
```

- [ ] **Step 5: Run tests and the type check**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: all PASS, tsc clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/create frontend/src/app/__tests__/create-page.test.tsx frontend/src/app/page.tsx
git commit -m "feat(fe): /create page — start a room with profile prefill"
```

---

### Task 7: Frontend — join page: prefill, already-member short-circuit, profile save-back

**Files:**
- Modify: `frontend/src/app/join/[token]/page.tsx`
- Test: `frontend/src/app/__tests__/join-page.test.tsx` (new)

**Interfaces:**
- Consumes: `getProfile`/`saveProfile`/`listRooms` (Task 2), `useSession().signIn/switchRoom` (Task 4).
- Produces: no new exports — behavior changes only.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/app/__tests__/join-page.test.tsx`:

```tsx
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import Join from "../join/[token]/page";
import { getProfile, saveProfile, upsertRoom } from "@/lib/rooms-store";

const push = vi.fn();
const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace }),
  useParams: () => ({ token: "invite-1" }),
}));

const signIn = vi.fn();
const switchRoom = vi.fn();
vi.mock("@/lib/session", () => ({ useSession: () => ({ signIn, switchRoom }) }));

const roomInfo = vi.fn();
const createAccount = vi.fn();
const identify = vi.fn();
vi.mock("@/lib/api", () => ({
  roomInfo: (...a: any[]) => roomInfo(...a),
  createAccount: (...a: any[]) => createAccount(...a),
  identify: (...a: any[]) => identify(...a),
}));

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  roomInfo.mockResolvedValue({ room_id: 5, name: "Lunch B", members: [] });
});

describe("Join page (multi-room)", () => {
  it("short-circuits to the room when this device already holds its token", async () => {
    upsertRoom({ roomId: 5, roomName: "Lunch B", token: "have-it" });
    render(<Join />);
    await waitFor(() => expect(switchRoom).toHaveBeenCalledWith(5));
    expect(replace).toHaveBeenCalledWith("/");
    expect(signIn).not.toHaveBeenCalled();
  });

  it("prefills the form from the saved profile", async () => {
    saveProfile({ nickname: "an", pin: "1234", display_name: "An", bank_code: "VCB" });
    render(<Join />);
    await waitFor(() => expect(screen.getByLabelText("Nickname")).toHaveValue("an"));
    expect(screen.getByLabelText("PIN")).toHaveValue("1234");
  });

  it("passes the room name to signIn and saves the profile back after joining", async () => {
    createAccount.mockResolvedValue({ token: "tok5", room_id: 5, member_id: 3 });
    render(<Join />);
    await screen.findByText(/Join/);
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "An" } });
    fireEvent.change(screen.getByLabelText("Nickname"), { target: { value: "an" } });
    fireEvent.change(screen.getByLabelText("PIN"), { target: { value: "9999" } });
    fireEvent.click(screen.getByRole("button", { name: /create & join/i }));

    await waitFor(() => expect(signIn).toHaveBeenCalledWith("tok5", 5, "Lunch B"));
    expect(getProfile()).toMatchObject({ nickname: "an", pin: "9999", display_name: "An" });
    expect(push).toHaveBeenCalledWith("/");
  });

  it("saves nickname and PIN back after sign-in mode too", async () => {
    identify.mockResolvedValue({ token: "tok5", room_id: 5 });
    render(<Join />);
    await screen.findByText(/Join/);
    fireEvent.change(screen.getByLabelText("Nickname"), { target: { value: "binh" } });
    fireEvent.change(screen.getByLabelText("PIN"), { target: { value: "1111" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(signIn).toHaveBeenCalledWith("tok5", 5, "Lunch B"));
    expect(getProfile()).toMatchObject({ nickname: "binh", pin: "1111" });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/app/__tests__/join-page.test.tsx`
Expected: FAIL — short-circuit/prefill/save-back behaviors don't exist yet (e.g. `switchRoom` never called; nickname field empty).

- [ ] **Step 3: Implement the behavior changes**

In `frontend/src/app/join/[token]/page.tsx`:

1. Add the import:

```ts
import { getProfile, listRooms, saveProfile } from "@/lib/rooms-store";
```

2. Destructure `switchRoom` too (line 37):

```ts
  const { signIn, switchRoom } = useSession();
```

3. Replace the `roomInfo` effect (lines 56-61) with a version that short-circuits when the device already has this room, and prefill from the saved profile:

```ts
  useEffect(() => {
    api
      .roomInfo(token)
      .then((r: Room) => {
        // Already a member on this device? Just make it the active room.
        if (listRooms().some((s) => s.roomId === r.room_id)) {
          switchRoom(r.room_id);
          router.replace("/");
          return;
        }
        setRoom(r);
      })
      .catch(() => setNotFound(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // Prefill from the client-saved profile so joining another room is one tap.
  useEffect(() => {
    const p = getProfile();
    setF((prev) => ({
      ...prev,
      display_name: p.display_name ?? "",
      nickname: p.nickname ?? "",
      pin: p.pin ?? "",
      bank_code: p.bank_code ?? "",
      account_number: p.account_number ?? "",
      account_holder: p.account_holder ?? "",
    }));
  }, []);
```

4. In `submit()` (lines 78-93), save the profile back on success. Replace the body of the `try` block with:

```ts
      const res =
        mode === "create"
          ? await api.createAccount(token, f)
          : await api.identify(token, { nickname: f.nickname, pin: f.pin });
      // Save-back: the local profile always holds the latest values used.
      // (f's keys are exactly the SavedProfile fields on this page.)
      if (mode === "create") {
        saveProfile(f);
      } else {
        saveProfile({ nickname: f.nickname, pin: f.pin });
      }
      signIn(res.token, res.room_id, room?.name ?? "");
      router.push("/");
```

(The `signIn(res.token, res.room_id, room?.name ?? "")` line was already updated in Task 4 — keep it.)

- [ ] **Step 4: Run tests and the type check**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: all PASS, tsc clean.

- [ ] **Step 5: Commit**

```bash
git add "frontend/src/app/join/[token]/page.tsx" frontend/src/app/__tests__/join-page.test.tsx
git commit -m "feat(fe): join page — profile prefill, already-member switch, save-back"
```

---

### Task 8: Frontend — ProfileDialog save-back

**Files:**
- Modify: `frontend/src/components/chat/room-view.tsx:213-257` (ProfileDialog: export it, save-back after `updateMe`)
- Test: `frontend/src/components/chat/__tests__/profile-dialog.test.tsx` (new)

**Interfaces:**
- Consumes: `saveProfile` (Task 2); existing `api.updateMe`.
- Produces: `export function ProfileDialog(...)` (exported for tests; previously module-private — no behavior change from exporting).

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/chat/__tests__/profile-dialog.test.tsx`:

```tsx
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ProfileDialog } from "../room-view";
import { getProfile } from "@/lib/rooms-store";

vi.mock("@/lib/session", () => ({ useSession: () => ({ signOut: vi.fn(), memberId: 1 }) }));

const updateMe = vi.fn();
vi.mock("@/lib/api", () => ({ updateMe: (...a: any[]) => updateMe(...a) }));

const member = {
  id: 1, display_name: "An", nickname: "an", claimed: true, has_bank: false,
  bank_code: "", account_number: "", account_holder: "",
} as any;

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});

describe("ProfileDialog save-back", () => {
  it("writes edited fields to the saved profile after a successful save", async () => {
    updateMe.mockResolvedValue({ ok: true });
    render(<ProfileDialog member={member} onClose={() => {}} onSaved={() => {}} />);
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "An Nguyen" } });
    fireEvent.change(screen.getByLabelText("Bank code"), { target: { value: "VCB" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.getByText("Saved")).toBeInTheDocument());
    expect(getProfile()).toMatchObject({ display_name: "An Nguyen", bank_code: "VCB" });
  });

  it("does not touch the saved profile when the save fails", async () => {
    updateMe.mockRejectedValue(new Error("boom"));
    render(<ProfileDialog member={member} onClose={() => {}} onSaved={() => {}} />);
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "X" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(getProfile()).toEqual({});
  });
});
```

Note: `room-view.tsx` imports several sibling components; if rendering `ProfileDialog` in isolation drags in modules that break under jsdom (e.g. `use-room`'s outbox), mock those imports the same way the existing `__tests__` files in this directory do — check `expense-draft-card.test.tsx` for the established mocking pattern first and mirror it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/chat/__tests__/profile-dialog.test.tsx`
Expected: FAIL — `ProfileDialog` is not exported (import resolves to `undefined`).

- [ ] **Step 3: Implement**

In `frontend/src/components/chat/room-view.tsx`:

1. Add to the imports:

```ts
import { saveProfile } from "@/lib/rooms-store";
```

2. Export the dialog — change line 213 `function ProfileDialog({` to `export function ProfileDialog({`.

3. In `save()` (lines 244-257), add the save-back right after `await api.updateMe(f);`:

```ts
      await api.updateMe(f);
      // Save-back (design 2026-07-22): the local profile mirrors the latest
      // values so joining the NEXT room prefills them. No fan-out to other
      // rooms' servers — their member records may drift; that's accepted.
      saveProfile({
        display_name: f.display_name,
        bank_code: f.bank_code,
        account_number: f.account_number,
        account_holder: f.account_holder,
      });
```

- [ ] **Step 4: Run tests and the type check**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: all PASS, tsc clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/chat/room-view.tsx frontend/src/components/chat/__tests__/profile-dialog.test.tsx
git commit -m "feat(fe): profile edits save back to the local profile"
```

---

### Task 9: Full verification + smoke

**Files:** none (verification only).

- [ ] **Step 1: Full backend suite**

Run: `cd backend && .venv/bin/python -m pytest`
Expected: all PASS (the LLM eval suite auto-skips without `RUN_LLM_EVAL=1`).

- [ ] **Step 2: Full frontend suite + type check + production build**

Run: `cd frontend && npx vitest run && npx tsc --noEmit && npm run build`
Expected: all tests PASS, tsc clean, build succeeds (confirms `/create` prerenders without touching localStorage).

- [ ] **Step 3: Manual smoke (run-chiatienan skill)**

Use the `run-chiatienan` skill to start the app, then walk this script in the browser:

1. Create room "A" via `/create` with nickname `an`, PIN `1234` → lands in room A, header shows "A".
2. Header menu → "Create a room" → form is prefilled with `an`/`1234` → create room "B" → lands in room B.
3. Header menu → shows "Switch room" list with "A" → switch → room A renders (messages/members of A only).
4. In room A, open the invite link in the same browser → it short-circuits back to `/` in room A (no join form).
5. Header menu → "Remove from this device" → confirm → falls back to room B.
6. Post a message in room B mentioning the room name to confirm isolation held throughout.

Expected: every step behaves as written; no console errors.

- [ ] **Step 4: Commit any leftover fixes, then finish**

If the smoke run surfaced fixes, commit them (`fix(fe): …`). Then use the superpowers:finishing-a-development-branch skill to wrap up the branch.
