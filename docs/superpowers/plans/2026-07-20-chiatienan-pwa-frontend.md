# chiatienan PWA — Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An installable PWA (lifted from the cursor-sdk sample's Next.js chat) that lets a member join a room via invite link, create an account (name, nickname, banking, PIN), and use a shared room chat where the agent replies to `@bot`.

**Architecture:** Next.js (App Router) served by Caddy at `/`; the backend (from the backend plan) at `/api/*` on the same origin, so the frontend uses **relative URLs** (no API base env). Session token in `localStorage` sent as `Authorization: Bearer`. Realtime uses a **fetch-stream SSE reader** (not `EventSource`, which can't send an auth header). Reuses the sample's chat rendering/Tailwind/ui primitives; replaces its single-user localStorage history with the server room stream.

**Tech Stack:** Next.js 16, React 19, TypeScript, Tailwind, `react-markdown`+`remark-gfm`, `lucide-react`, Vitest. Backend contract is the backend plan's §5 routes.

## Global Constraints

- **Same-origin relative API calls** (`/api/...`); no `NEXT_PUBLIC_API_URL`.
- **Auth:** `localStorage["chiatienan.token"]` + `localStorage["chiatienan.room_id"]`; every API call (incl. SSE) sends `Authorization: Bearer <token>`.
- **SSE via `fetch` + `ReadableStream`** (auth header required) — never `EventSource`. Reconnect with `?since=<lastId>`.
- **PWA installable**: web manifest + service worker + 192/512 icons; standalone display. Offline not required.
- **Identity is not security** (spec D7/D8) — no client-side crypto; the invite link is the boundary.
- Bot replies render from the message's structured `attachments` (`type: "settlement"|"meal"`), not re-parsed text.
- Reuse the sample under `reference/sample-cursor-sdk-with-image/frontend/` as the starting point; keep its renderers/ui, drop model-selector + localStorage conversation history.

## File Structure

- `frontend/` — lifted from the sample; adapted below.
- `frontend/src/lib/api.ts` — **rewrite**: typed client for rooms/accounts/me/messages + `streamRoom()` SSE reader; token storage.
- `frontend/src/lib/session.tsx` — **create**: `SessionProvider` + `useSession()` (token, room_id, login/logout).
- `frontend/src/hooks/use-room.ts` — **create**: load history, open SSE, post message, typing state (replaces `use-chat.ts`).
- `frontend/src/components/chat/message-list.tsx` — **create**: renders human + bot messages.
- `frontend/src/components/chat/bot-message.tsx` — **create**: markdown body + settlement/meal attachment renderers (QR images, transfer table).
- `frontend/src/components/chat/composer.tsx` — **adapt** from the sample composer (text + image attach).
- `frontend/src/app/page.tsx` — **rewrite**: gate → room chat or landing.
- `frontend/src/app/join/[token]/page.tsx` — **create**: room info + create-account / identify.
- `frontend/src/app/profile/page.tsx` — **create**: profile + banking editor.
- `frontend/public/manifest.webmanifest`, `frontend/public/sw.js`, `frontend/public/icon-192.png`, `frontend/public/icon-512.png` — **create**.
- `frontend/src/lib/sse.ts` + `frontend/src/lib/__tests__/sse.test.ts` — **create**: SSE line parser (unit-tested).
- **Drop:** `use-conversations.ts`, `chat-storage.ts`, `conversation-list.tsx`, model-selector bits, `chat-payload.ts` (AG-UI single-turn).
- `frontend/Dockerfile` — reuse; `docker-compose.yml` + `Caddyfile` — **modify** (Task 9).

---

## Task 1: Lift the sample frontend + strip single-user bits

**Files:**
- Create: `frontend/**` (copied from `reference/sample-cursor-sdk-with-image/frontend/`)
- Delete after copy: `src/hooks/use-conversations.ts`, `src/lib/chat-storage.ts`, `src/lib/chat-payload.ts`, `src/lib/__tests__/chat-payload.test.ts`, `src/components/chat/conversation-list.tsx`, `src/hooks/use-chat.ts`

- [ ] **Step 1: Copy the sample frontend into place**

```bash
cp -r reference/sample-cursor-sdk-with-image/frontend ./frontend
rm frontend/src/hooks/use-conversations.ts frontend/src/lib/chat-storage.ts \
   frontend/src/lib/chat-payload.ts frontend/src/lib/__tests__/chat-payload.test.ts \
   frontend/src/components/chat/conversation-list.tsx frontend/src/hooks/use-chat.ts
```

- [ ] **Step 2: Install deps and verify the base builds**

Run: `cd frontend && npm install && npm run build`
Expected: build fails only on the now-missing imports we deleted (referenced by `chat-sidebar.tsx`/`page.tsx`) — that's expected; later tasks replace them. If it fails for other reasons (toolchain), fix those now.

- [ ] **Step 3: Commit the lift**

```bash
git add frontend && git commit -m "chore(frontend): lift sample chat; drop single-user history/model bits"
```

---

## Task 2: SSE line parser (pure, unit-tested)

**Files:**
- Create: `frontend/src/lib/sse.ts`, `frontend/src/lib/__tests__/sse.test.ts`

**Interfaces:**
- Produces: `parseSSE(buffer: string): { events: any[]; rest: string }` — splits on `\n\n`, strips `data: `, JSON-parses each; returns leftover partial in `rest`.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/lib/__tests__/sse.test.ts
import { describe, it, expect } from "vitest";
import { parseSSE } from "../sse";

describe("parseSSE", () => {
  it("parses complete events and keeps the partial remainder", () => {
    const { events, rest } = parseSSE(
      'data: {"type":"message","id":1}\n\ndata: {"type":"bot.typing"}\n\ndata: {"type":"mess'
    );
    expect(events).toEqual([{ type: "message", id: 1 }, { type: "bot.typing" }]);
    expect(rest).toBe('data: {"type":"mess');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/__tests__/sse.test.ts`
Expected: FAIL — cannot find `../sse`.

- [ ] **Step 3: Write minimal implementation**

```ts
// frontend/src/lib/sse.ts
export function parseSSE(buffer: string): { events: any[]; rest: string } {
  const parts = buffer.split("\n\n");
  const rest = parts.pop() ?? "";
  const events: any[] = [];
  for (const chunk of parts) {
    const line = chunk.split("\n").find((l) => l.startsWith("data:"));
    if (!line) continue;
    try { events.push(JSON.parse(line.slice(5).trim())); } catch { /* skip malformed */ }
  }
  return { events, rest };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/__tests__/sse.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/sse.ts frontend/src/lib/__tests__/sse.test.ts
git commit -m "feat(frontend): SSE line parser"
```

---

## Task 3: API client + token storage

**Files:**
- Rewrite: `frontend/src/lib/api.ts`

**Interfaces:**
- Produces: `getToken()/setToken()/getRoomId()/setRoomId()/clearSession()`; `roomInfo(token)`, `createAccount(token, body)`, `identify(token, body)`, `getMe()`, `updateMe(body)`, `getMessages(roomId, since)`, `postMessage(roomId, body, images?)`, and `streamRoom(roomId, since, onEvent, signal)` (fetch-stream using `parseSSE`). All requests attach `Authorization: Bearer` when a token exists; throw `ApiError` on non-2xx.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/lib/__tests__/api.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import * as api from "../api";

beforeEach(() => { localStorage.clear(); vi.restoreAllMocks(); });

it("attaches bearer token and posts a message", async () => {
  api.setToken("t123");
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ ok: true, id: 9 }), { status: 200 })
  );
  vi.stubGlobal("fetch", fetchMock);
  const res = await api.postMessage(1, "hi");
  expect(res.id).toBe(9);
  const [, init] = fetchMock.mock.calls[0];
  expect((init.headers as any).Authorization).toBe("Bearer t123");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/__tests__/api.test.ts`
Expected: FAIL — functions not exported.

- [ ] **Step 3: Write minimal implementation**

```ts
// frontend/src/lib/api.ts
import { parseSSE } from "./sse";

const TOKEN = "chiatienan.token";
const ROOM = "chiatienan.room_id";
export const getToken = () => (typeof localStorage !== "undefined" ? localStorage.getItem(TOKEN) : null);
export const setToken = (t: string) => localStorage.setItem(TOKEN, t);
export const getRoomId = () => Number(localStorage.getItem(ROOM) || 0) || null;
export const setRoomId = (id: number) => localStorage.setItem(ROOM, String(id));
export const clearSession = () => { localStorage.removeItem(TOKEN); localStorage.removeItem(ROOM); };

export class ApiError extends Error { constructor(public status: number, msg: string) { super(msg); } }

async function req(path: string, init: RequestInit = {}) {
  const headers: Record<string, string> = { "content-type": "application/json", ...(init.headers as any) };
  const tok = getToken();
  if (tok) headers.Authorization = `Bearer ${tok}`;
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) throw new ApiError(res.status, (await res.json().catch(() => ({}))).detail || res.statusText);
  return res.status === 204 ? null : res.json();
}

export const roomInfo = (t: string) => req(`/api/rooms/${t}`);
export const createAccount = (t: string, b: any) => req(`/api/rooms/${t}/accounts`, { method: "POST", body: JSON.stringify(b) });
export const identify = (t: string, b: any) => req(`/api/rooms/${t}/identify`, { method: "POST", body: JSON.stringify(b) });
export const getMe = () => req(`/api/me`);
export const updateMe = (b: any) => req(`/api/me`, { method: "PUT", body: JSON.stringify(b) });
export const getMessages = (roomId: number, since = 0) => req(`/api/rooms/${roomId}/messages?since=${since}`);
export const postMessage = (roomId: number, body: string, images?: any[]) =>
  req(`/api/rooms/${roomId}/messages`, { method: "POST", body: JSON.stringify({ body, images }) });

export async function streamRoom(roomId: number, since: number, onEvent: (e: any) => void, signal: AbortSignal) {
  const res = await fetch(`/api/rooms/${roomId}/stream?since=${since}`, {
    headers: { Authorization: `Bearer ${getToken()}` }, signal,
  });
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/__tests__/api.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/__tests__/api.test.ts
git commit -m "feat(frontend): API client + bearer + SSE fetch-stream"
```

---

## Task 4: Session context + route gating

**Files:**
- Create: `frontend/src/lib/session.tsx`
- Modify: `frontend/src/app/layout.tsx` (wrap with `SessionProvider`), `frontend/src/app/page.tsx` (gate)

**Interfaces:**
- Produces: `SessionProvider`, `useSession() -> { token, roomId, ready, signIn(token, roomId), signOut() }`.

- [ ] **Step 1: Write the implementation (provider)**

```tsx
// frontend/src/lib/session.tsx
"use client";
import { createContext, useContext, useEffect, useState } from "react";
import { getToken, getRoomId, setToken, setRoomId, clearSession } from "./api";

type Ctx = { token: string | null; roomId: number | null; ready: boolean;
  signIn: (t: string, r: number) => void; signOut: () => void };
const SessionCtx = createContext<Ctx>(null as any);
export const useSession = () => useContext(SessionCtx);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [token, setTok] = useState<string | null>(null);
  const [roomId, setRid] = useState<number | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => { setTok(getToken()); setRid(getRoomId()); setReady(true); }, []);
  const signIn = (t: string, r: number) => { setToken(t); setRoomId(r); setTok(t); setRid(r); };
  const signOut = () => { clearSession(); setTok(null); setRid(null); };
  return <SessionCtx.Provider value={{ token, roomId, ready, signIn, signOut }}>{children}</SessionCtx.Provider>;
}
```

- [ ] **Step 2: Wire layout + gate page**

In `layout.tsx`, wrap `{children}` with `<SessionProvider>`. Rewrite `page.tsx`:

```tsx
// frontend/src/app/page.tsx
"use client";
import { useSession } from "@/lib/session";
import RoomView from "@/components/chat/room-view";  // created in Task 6

export default function Home() {
  const { token, roomId, ready } = useSession();
  if (!ready) return null;
  if (!token || !roomId)
    return <main className="p-8 max-w-md mx-auto text-center">
      <h1 className="text-xl font-semibold">chiatienan</h1>
      <p className="mt-2 text-sm opacity-70">Mở link mời từ admin để tham gia phòng.</p>
    </main>;
  return <RoomView roomId={roomId} />;
}
```

- [ ] **Step 3: Verify build/typecheck**

Run: `cd frontend && npm run build`
Expected: fails only on the not-yet-created `room-view` import (Task 6) — acceptable mid-plan; other type errors must be fixed.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/session.tsx frontend/src/app/layout.tsx frontend/src/app/page.tsx
git commit -m "feat(frontend): session context + route gate"
```

---

## Task 5: Join / identify screen

**Files:**
- Create: `frontend/src/app/join/[token]/page.tsx`

**Interfaces:**
- Consumes: `roomInfo`, `createAccount`, `identify` from `api`; `useSession().signIn`.

- [ ] **Step 1: Implement the screen**

```tsx
// frontend/src/app/join/[token]/page.tsx
"use client";
import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import * as api from "@/lib/api";
import { useSession } from "@/lib/session";

export default function Join() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const { signIn } = useSession();
  const [room, setRoom] = useState<{ name: string; room_id: number } | null>(null);
  const [mode, setMode] = useState<"create" | "login">("create");
  const [f, setF] = useState({ display_name: "", nickname: "", pin: "", bank_code: "", account_number: "", account_holder: "" });
  const [err, setErr] = useState("");

  useEffect(() => { api.roomInfo(token).then(setRoom).catch(() => setErr("Link không hợp lệ.")); }, [token]);

  async function submit() {
    setErr("");
    try {
      const res = mode === "create"
        ? await api.createAccount(token, f)
        : await api.identify(token, { nickname: f.nickname, pin: f.pin });
      signIn(res.token, res.room_id);
      router.push("/");
    } catch (e: any) { setErr(e.message || "Lỗi"); }
  }

  if (err && !room) return <main className="p-8 text-center">{err}</main>;
  if (!room) return null;
  return (
    <main className="p-6 max-w-md mx-auto space-y-3">
      <h1 className="text-lg font-semibold">Tham gia “{room.name}”</h1>
      <div className="flex gap-2 text-sm">
        <button onClick={() => setMode("create")} className={mode==="create"?"font-semibold":""}>Tạo tài khoản</button>
        <button onClick={() => setMode("login")} className={mode==="login"?"font-semibold":""}>Tôi đã có</button>
      </div>
      {mode === "create" && <input placeholder="Tên hiển thị" value={f.display_name} onChange={e=>setF({...f,display_name:e.target.value})} className="border p-2 w-full rounded" />}
      <input placeholder="Biệt danh" value={f.nickname} onChange={e=>setF({...f,nickname:e.target.value})} className="border p-2 w-full rounded" />
      <input placeholder="PIN" value={f.pin} onChange={e=>setF({...f,pin:e.target.value})} className="border p-2 w-full rounded" />
      {mode === "create" && <>
        <input placeholder="Mã ngân hàng (vd VCB)" value={f.bank_code} onChange={e=>setF({...f,bank_code:e.target.value})} className="border p-2 w-full rounded" />
        <input placeholder="Số tài khoản" value={f.account_number} onChange={e=>setF({...f,account_number:e.target.value})} className="border p-2 w-full rounded" />
        <input placeholder="Tên chủ tài khoản" value={f.account_holder} onChange={e=>setF({...f,account_holder:e.target.value})} className="border p-2 w-full rounded" />
      </>}
      {err && <p className="text-red-600 text-sm">{err}</p>}
      <button onClick={submit} className="bg-black text-white rounded p-2 w-full">{mode==="create"?"Tạo & vào phòng":"Vào phòng"}</button>
    </main>
  );
}
```

- [ ] **Step 2: Verify build** — Run: `cd frontend && npm run build` (join route compiles). **Commit:** `git commit -am "feat(frontend): join/identify screen"`.

---

## Task 6: Room chat view (history + SSE + composer + bot rendering)

**Files:**
- Create: `frontend/src/hooks/use-room.ts`, `frontend/src/components/chat/room-view.tsx`, `message-list.tsx`, `bot-message.tsx`, `composer.tsx`

**Interfaces:**
- `use-room.ts`: `useRoom(roomId) -> { messages, typing, send(text, images?) }` — initial `getMessages`, then `streamRoom` appending events (dedupe by id), tracks `bot.typing`/`bot.done`.
- `bot-message.tsx`: renders `body` (markdown) + `attachments.type === "settlement"` (transfer rows with `qr_url` images) or `"meal"` (breakdown).

- [ ] **Step 1: Write the failing test (hook reducer logic)**

```ts
// frontend/src/hooks/__tests__/merge.test.ts
import { describe, it, expect } from "vitest";
import { mergeEvent } from "../use-room";

describe("mergeEvent", () => {
  it("appends messages, dedupes by id, toggles typing", () => {
    let s = { messages: [] as any[], typing: false };
    s = mergeEvent(s, { type: "message", id: 1, body: "hi" });
    s = mergeEvent(s, { type: "message", id: 1, body: "hi" }); // dup
    s = mergeEvent(s, { type: "bot.typing" });
    expect(s.messages.map(m => m.id)).toEqual([1]);
    expect(s.typing).toBe(true);
    s = mergeEvent(s, { type: "message", id: 2, kind: "bot", body: "pong" });
    s = mergeEvent(s, { type: "bot.done" });
    expect(s.typing).toBe(false);
    expect(s.messages.length).toBe(2);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/hooks/__tests__/merge.test.ts`
Expected: FAIL — `mergeEvent` not exported.

- [ ] **Step 3: Implement `use-room.ts` (incl. exported `mergeEvent`)**

```ts
// frontend/src/hooks/use-room.ts
"use client";
import { useEffect, useRef, useState } from "react";
import * as api from "@/lib/api";

export type RoomState = { messages: any[]; typing: boolean };
export function mergeEvent(s: RoomState, e: any): RoomState {
  if (e.type === "bot.typing") return { ...s, typing: true };
  if (e.type === "bot.done") return { ...s, typing: false };
  if (e.type === "message") {
    if (s.messages.some(m => m.id === e.id)) return s;
    const { type, ...msg } = e;
    return { ...s, messages: [...s.messages, msg] };
  }
  return s;
}

export function useRoom(roomId: number) {
  const [state, setState] = useState<RoomState>({ messages: [], typing: false });
  const lastId = useRef(0);
  useEffect(() => {
    const ac = new AbortController();
    let stop = false;
    (async () => {
      const { messages } = await api.getMessages(roomId, 0);
      messages.forEach((m: any) => (lastId.current = Math.max(lastId.current, m.id)));
      setState({ messages, typing: false });
      while (!stop) {
        try {
          await api.streamRoom(roomId, lastId.current, (e) => {
            if (e.id) lastId.current = Math.max(lastId.current, e.id);
            setState((s) => mergeEvent(s, e));
          }, ac.signal);
        } catch { if (!stop) await new Promise(r => setTimeout(r, 2000)); } // reconnect
      }
    })();
    return () => { stop = true; ac.abort(); };
  }, [roomId]);
  const send = (text: string, images?: any[]) => api.postMessage(roomId, text, images);
  return { messages: state.messages, typing: state.typing, send };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/hooks/__tests__/merge.test.ts`
Expected: PASS.

- [ ] **Step 5: Implement the view + renderers**

`bot-message.tsx` renders `ReactMarkdown` for `body`; if `attachments?.type === "settlement"`, list each transfer (`from_name → to_name: amount`) with `<img src={qr_url}>`; if `"meal"`, show payer + shares. `message-list.tsx` maps messages: human bubble (author.name + body) vs `<BotMessage>` for `kind === "bot"`. `composer.tsx` (adapt sample): textarea + image attach (base64 → `{data, mimeType}` list) + send. `room-view.tsx` wires `useRoom` + list + composer + a "bot đang trả lời…" indicator when `typing`.

- [ ] **Step 6: Verify build + tests**

Run: `cd frontend && npm run build && npx vitest run`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/hooks frontend/src/components/chat
git commit -m "feat(frontend): room chat view, SSE hook, bot/settlement renderers"
```

---

## Task 7: Profile / banking editor

**Files:**
- Create: `frontend/src/app/profile/page.tsx`

- [ ] **Step 1: Implement** a form that loads `getMe()`, edits `display_name`/`bank_code`/`account_number`/`account_holder`, saves via `updateMe()`, plus a "Đăng xuất" button calling `useSession().signOut()`. Link to it from `room-view` header.
- [ ] **Step 2: Verify build** — Run: `cd frontend && npm run build`. **Commit:** `git commit -am "feat(frontend): profile + banking editor"`.

---

## Task 8: PWA — manifest, icons, service worker

**Files:**
- Create: `frontend/public/manifest.webmanifest`, `frontend/public/sw.js`, `frontend/public/icon-192.png`, `frontend/public/icon-512.png`
- Modify: `frontend/src/app/layout.tsx` (manifest link + SW registration)

- [ ] **Step 1: Add the manifest**

```json
// frontend/public/manifest.webmanifest
{
  "name": "chiatienan", "short_name": "chiatienan", "start_url": "/", "display": "standalone",
  "background_color": "#ffffff", "theme_color": "#4B3FBB",
  "icons": [
    { "src": "/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

- [ ] **Step 2: Add a minimal service worker** (network-first; enables install)

```js
// frontend/public/sw.js
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {}); // pass-through; presence enables installability
```

- [ ] **Step 3: Link manifest + register SW** in `layout.tsx`: add `<link rel="manifest" href="/manifest.webmanifest">` and `<meta name="theme-color" content="#4B3FBB">` in `<head>`; register the SW in a client effect (`if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js")`).

- [ ] **Step 4: Generate icons** — 192×192 and 512×512 PNGs (solid `#4B3FBB` with a bowl/₫ glyph is fine). Run: `cd frontend && npm run build` — confirm no manifest/asset errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/public/manifest.webmanifest frontend/public/sw.js frontend/public/icon-192.png frontend/public/icon-512.png frontend/src/app/layout.tsx
git commit -m "feat(frontend): installable PWA (manifest, SW, icons)"
```

---

## Task 9: Deploy — frontend container + Caddy routing

**Files:**
- Modify: `docker-compose.yml`, `Caddyfile`

- [ ] **Step 1: Add the frontend service** to `docker-compose.yml`:

```yaml
  frontend:
    build: ./frontend
    restart: unless-stopped
    expose: ["3000"]
    depends_on: [backend]
```

- [ ] **Step 2: Route in `Caddyfile`** — `/api/*` (and the SSE path) to backend, everything else to frontend:

```
{$CADDY_DOMAIN} {
	@api path /api/* /internal/*
	reverse_proxy @api backend:8000
	reverse_proxy frontend:3000
}
```

> SSE note: Caddy streams `text/event-stream` fine by default (no buffering config needed).

- [ ] **Step 3: Deploy + verify** (from a non-office network):

```bash
ssh -i ~/.ssh/digitalocean-openclaw root@165.22.246.208 \
  'cd /opt/chiatienan && git pull && docker compose up -d --build'
```
Open `https://chiatienan.duckdns.org` on a phone → install PWA → open an admin-created invite link → create account → send `@bot ghi 100k, an và binh` → bot reply appears for all members.

- [ ] **Step 4: Commit** any routing fixes.

---

## Self-Review

**Spec coverage:** D1 PWA (Tasks 1,8,9); D2 reuse sample chat (Task 1,6); D6 SSE fan-out consumed (Tasks 2,3,6); D7/D8 nickname+PIN, link-boundary, no client crypto (Tasks 3,5); D9 `@bot` reply rendering (Task 6); §7 screens (Tasks 4,5,6,7) + PWA (Task 8); §9 deploy (Task 9). Backend contract (backend plan §5) consumed exactly in Task 3.

**Placeholder scan:** none — icon generation (Task 8 Step 4) is a concrete asset step, not a code placeholder.

**Type consistency:** `signIn(token, roomId)` (Task 4) matches `createAccount`/`identify` returns `{token, room_id}` (Task 3, 5). `streamRoom(roomId, since, onEvent, signal)` used identically in Tasks 3 & 6. `mergeEvent(state, event)` signature consistent (Task 6). Message shape `{id, kind, body, attachments, author}` matches backend `message_to_dict` (backend Task 9).
