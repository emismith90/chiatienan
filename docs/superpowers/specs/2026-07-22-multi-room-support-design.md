# Multi-room support (client-side) — Design

**Date:** 2026-07-22
**Status:** Approved for planning

## Problem

A person can currently be in exactly one room. The backend is already
room-scoped everywhere (every table carries `room_id`, all queries filter by
it, `_check_room` enforces token↔room match), but a session token is bound to
one member → one room, and the frontend stores a single `token` + `room_id`
pair in localStorage. Room creation is admin-only.

Goal: a person can create and join multiple rooms and switch between them.
Rooms stay fully isolated — scope, logic, and calculations per room are
unchanged.

## Decisions

1. **No server-side global identity.** Members stay per-room; the client holds
   a `room_id → token` map. (Rejected: a cross-room `User` entity — overkill
   for a lunch group.)
2. **Anyone can create a room** via a new public endpoint. The admin-only
   provisioning endpoint stays.
3. **Client-saved profile** (localStorage) prefills join/create forms so
   joining another room is one tap. Profile edits in a room **save back** to
   the local copy, but there is **no fan-out** to other rooms' servers —
   per-room member records may drift; that is accepted.
4. **Active room = last-accessed pointer.** The single `/` page renders the
   most recently accessed room. No URL-per-room routing. One-room users see
   no difference; that path stays the priority UX.

## Design

### 1. Backend (one new endpoint, nothing else)

`POST /api/rooms/create` — public, no admin password.

- Request: `{room_name, nickname, display_name, pin?, bank fields…}`
  (member fields identical to the join/create-account flow).
- Behavior: creates the room + its first member + a session in one call.
  Internally reuses `rooms.create_room` + `accounts.create_account`.
- Response: `{token, room_id, room_name, invite_token}`.

The existing admin `POST /api/rooms` remains for provisioning. All isolation
logic (`_check_room` in `backend/app/main.py`, room-scoped queries) is
untouched.

### 2. Frontend storage (localStorage)

- `chiatienan.rooms` — JSON array of
  `{roomId, roomName, token, lastAccessAt}`. Replaces the single token/room
  pair. Legacy keys `chiatienan.token` + `chiatienan.room_id` are migrated
  into the list on first load (room name fetched lazily), then removed.
- `chiatienan.profile` — `{nickname, displayName, pin, bank…}`. Written on
  every join, room-create, and profile edit (save-back, so the local copy is
  always the latest). Read to prefill join/create forms. Never synced to
  other rooms' servers.

### 3. Active room + switcher

- The home page `/` renders whichever entry has the newest `lastAccessAt`.
- A room switcher (small dropdown in the room header) renders **only when the
  list has more than one room**. Switching bumps `lastAccessAt` and
  re-renders `RoomView`.
- The switcher menu also holds:
  - **Create a room** → create flow (section 4).
  - **Remove from this device** → deletes the local entry + best-effort
    server sign-out; the server-side member is untouched.

### 4. Join & create flows

- `/join/[token]`: if a token for that room is already stored → just switch
  to it (bump `lastAccessAt`, go home). Otherwise the join form is prefilled
  from the saved profile. On success, append to the rooms list and save the
  profile back.
- **Create room**: reachable from the switcher menu and from the "no room
  yet" landing state. Form = room name + profile fields (prefilled). Calls
  `POST /api/rooms/create`, appends to the list, becomes the active room.

### 5. Error handling

If a stored token gets a 401 (member removed, room deleted), drop that entry
from the list and fall back to the next most-recent room, or the landing
state if none remain. No retry loops.

### 6. Testing

- **Backend:** public create endpoint — creates room + member + session;
  input validation; returned invite token admits a second joiner; created
  room is isolated from existing rooms.
- **Frontend:** storage migration from legacy keys; switcher hidden at one
  room / shown at two; switch updates active room; join prefill from saved
  profile; already-member join short-circuits to switch; profile-edit
  save-back; 401 eviction and fallback.

## Out of scope

- Server-side user identity spanning rooms
- Profile fan-out / cross-room sync
- URL-per-room routing, deep links
- Cross-device sync of the room list or profile
