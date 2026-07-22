# Money-Reporting Overhaul — Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the new money data — a personal statement card, a chronological summary card, and an always-on ledger panel (desktop right column + phone drawer) that stays live over SSE.

**Architecture:** New `getLedger` API call + a `useLedger(roomId, version)` hook that refetches when the room stream reports `ledger:changed`. Shared presentational components (`BalanceBars`, `TransactionTimeline`) power both the chat cards (`StatementCard`, `SummaryCard`, dispatched by `BotMessage`) and the `LedgerPanel` in `RoomView`.

**Tech Stack:** Next.js 16 / React 19, TypeScript, Tailwind (CSS vars `--accent`/`--border`/`--bg-*`/`--text-*`), Vitest + @testing-library/react.

**Depends on backend plan:** attachment shapes `{type:"statement"|"summary", …}` and `GET /api/rooms/{id}/ledger` + the `ledger:changed` SSE event. See [`2026-07-22-money-reporting-backend.md`](2026-07-22-money-reporting-backend.md).

**Spec:** [`docs/superpowers/specs/2026-07-22-bot-money-reporting-overhaul-design.md`](../specs/2026-07-22-bot-money-reporting-overhaul-design.md)

## Global Constraints

- **Numbers are display-only here** — format with `fmt()` from `@/lib/format` (`Intl` vi-VN). Never compute a money value; render exactly what the API/attachment sends. Bar *widths* are layout math on the given integers, not new money values.
- **Theme-aware:** use existing CSS vars (`--accent`, `--accent-text`, `--border`, `--bg-base`, `--bg-surface`, `--text-primary`, `--text-secondary`); no hard-coded colors except the pos/neg accents defined once in `BalanceBars`.
- **Mobile-first:** chat stays primary on phones. The panel is a right column ≥ `lg`, a slide-over drawer below.
- **Tests:** TDD — write the failing Vitest test first. Run from `frontend/`: `npm test -- <file>`. Follow the existing `__tests__/*.test.tsx` style (see `balance-table.test.tsx`).
- **Signed amounts:** balances display with an explicit sign; reuse the `+100.000 / -100.000` convention already in `BalanceTable`.

## File Structure

- `frontend/src/lib/api.ts` — **modify**: add `getLedger`; export `LedgerData`/`TimelineEvent`/`BalanceRow` types.
- `frontend/src/hooks/use-room.ts` — **modify**: `mergeEvent` tracks `ledgerVersion`; `useRoom` returns it.
- `frontend/src/hooks/use-ledger.ts` — **create**: `useLedger(roomId, version)`.
- `frontend/src/components/chat/balance-bars.tsx` — **create**: shared bar chart.
- `frontend/src/components/chat/transaction-timeline.tsx` — **create**: shared timeline list.
- `frontend/src/components/chat/statement-card.tsx` — **create**.
- `frontend/src/components/chat/summary-card.tsx` — **create**.
- `frontend/src/components/chat/ledger-panel.tsx` — **create**.
- `frontend/src/components/chat/bot-message.tsx` — **modify**: dispatch `statement`/`summary`.
- `frontend/src/components/chat/room-view.tsx` — **modify**: desktop column + phone drawer.
- `frontend/src/components/chat/__tests__/` — **create** matching tests.

---

### Task 1: `getLedger` API + shared types

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Test: `frontend/src/lib/__tests__/api-ledger.test.ts` (new)

**Interfaces:**
- Produces:
  - `type BalanceRow = { id: number; name: string; balance: number }`
  - `type TimelineEvent = { kind: "meal"; meal_id: number; payer_id: number; payer_name: string; dish: string | null; occurred_on: string; total: number; participant_ids: number[]; created_at: string } | { kind: "payment"; payment_id: number; from_id: number; to_id: number; from_name: string; to_name: string; amount: number; occurred_on: string; created_at: string }`
  - `type LedgerData = { period: { from: string | null; to: string; keyword: string }; balances: BalanceRow[]; timeline: TimelineEvent[] }`
  - `getLedger(roomId: number, period?: string) => Promise<LedgerData>`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/__tests__/api-ledger.test.ts`:

```typescript
import { describe, expect, it, vi, afterEach } from "vitest";
import { getLedger } from "../api";

afterEach(() => vi.restoreAllMocks());

describe("getLedger", () => {
  it("GETs the room ledger with the period query", async () => {
    const body = { period: { from: null, to: "2026-07-22", keyword: "since_last" }, balances: [], timeline: [] };
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }),
    );
    const data = await getLedger(3);
    expect(spy).toHaveBeenCalledWith("/api/rooms/3/ledger?period=since_last", expect.anything());
    expect(data.period.keyword).toBe("since_last");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- src/lib/__tests__/api-ledger.test.ts`
Expected: FAIL — `getLedger` is not exported.

- [ ] **Step 3: Write minimal implementation**

In `frontend/src/lib/api.ts`, add the types and function (after `getMembers`):

```typescript
export type BalanceRow = { id: number; name: string; balance: number };

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

export type LedgerData = {
  period: { from: string | null; to: string; keyword: string };
  balances: BalanceRow[];
  timeline: TimelineEvent[];
};

export const getLedger = (roomId: number, period = "since_last"): Promise<LedgerData> =>
  req(`/api/rooms/${roomId}/ledger?period=${period}`);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- src/lib/__tests__/api-ledger.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/__tests__/api-ledger.test.ts
git commit -m "feat(fe): getLedger API + ledger types"
```

---

### Task 2: `ledgerVersion` in the room stream + `useLedger` hook

**Files:**
- Modify: `frontend/src/hooks/use-room.ts`
- Create: `frontend/src/hooks/use-ledger.ts`
- Test: `frontend/src/hooks/__tests__/ledger-version.test.ts` (new)

**Interfaces:**
- Consumes: `mergeEvent` (extended), `api.getLedger`.
- Produces:
  - `mergeEvent` increments `state.ledgerVersion` on `{type:"ledger:changed"}`; `useRoom` returns `ledgerVersion: number`.
  - `useLedger(roomId: number, version: number) => { data: LedgerData | null; loading: boolean }` — fetches on mount and whenever `roomId`/`version` changes.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/hooks/__tests__/ledger-version.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { mergeEvent } from "../use-room";

const base = { messages: [], typing: false, timelines: {}, activeTurn: null, hasMore: false };

describe("mergeEvent ledger:changed", () => {
  it("bumps ledgerVersion", () => {
    const s1 = mergeEvent(base as any, { type: "ledger:changed" });
    expect(s1.ledgerVersion).toBe(1);
    const s2 = mergeEvent(s1, { type: "ledger:changed" });
    expect(s2.ledgerVersion).toBe(2);
  });

  it("ignores unrelated events", () => {
    const s1 = mergeEvent(base as any, { type: "bot.typing" });
    expect(s1.ledgerVersion ?? 0).toBe(0);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- src/hooks/__tests__/ledger-version.test.ts`
Expected: FAIL — `ledgerVersion` is `undefined` after `ledger:changed`.

- [ ] **Step 3: Write minimal implementation**

In `frontend/src/hooks/use-room.ts`:

Add `ledgerVersion?: number;` to the `RoomState` type. Add this branch near the top of `mergeEvent` (before the `message` branch):

```typescript
  if (e.type === "ledger:changed") return { ...s, ledgerVersion: (s.ledgerVersion ?? 0) + 1 };
```

Add `ledgerVersion` to the object returned by `useRoom`:

```typescript
    ledgerVersion: state.ledgerVersion ?? 0,
```

Create `frontend/src/hooks/use-ledger.ts`:

```typescript
"use client";
import { useEffect, useState } from "react";
import * as api from "@/lib/api";
import type { LedgerData } from "@/lib/api";

/** Fetches the room ledger; refetches whenever `version` changes (bumped by the
 * room stream's `ledger:changed` event) so the panel stays live without its own
 * SSE connection. */
export function useLedger(roomId: number, version: number) {
  const [data, setData] = useState<LedgerData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .getLedger(roomId)
      .then((d) => live && setData(d))
      .catch(() => {})
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [roomId, version]);

  return { data, loading };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- src/hooks/__tests__/ledger-version.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/use-room.ts frontend/src/hooks/use-ledger.ts frontend/src/hooks/__tests__/ledger-version.test.ts
git commit -m "feat(fe): track ledgerVersion from SSE + useLedger hook"
```

---

### Task 3: Shared `BalanceBars` + `TransactionTimeline`

**Files:**
- Create: `frontend/src/components/chat/balance-bars.tsx`, `frontend/src/components/chat/transaction-timeline.tsx`
- Test: `frontend/src/components/chat/__tests__/balance-bars.test.tsx`, `.../transaction-timeline.test.tsx`

**Interfaces:**
- Consumes: `fmt`, `BalanceRow`, `TimelineEvent`.
- Produces:
  - `BalanceBars({ rows, selfId }: { rows: BalanceRow[]; selfId?: number | null })`
  - `TransactionTimeline({ events }: { events: TimelineEvent[] })`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/chat/__tests__/balance-bars.test.tsx`:

```tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { BalanceBars } from "../balance-bars";

const rows = [
  { id: 9, name: "Giang", balance: 89000 },
  { id: 5, name: "Trang", balance: -75000 },
];

describe("BalanceBars", () => {
  it("renders nothing when empty", () => {
    const { container } = render(<BalanceBars rows={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
  it("renders signed balances and marks self", () => {
    render(<BalanceBars rows={rows} selfId={9} />);
    expect(screen.getByText("+89.000")).toBeInTheDocument();
    expect(screen.getByText("-75.000")).toBeInTheDocument();
    expect(screen.getByText("Giang")).toHaveAttribute("data-self", "true");
  });
});
```

Create `frontend/src/components/chat/__tests__/transaction-timeline.test.tsx`:

```tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { TransactionTimeline } from "../transaction-timeline";

const events = [
  { kind: "meal", meal_id: 2, payer_id: 6, payer_name: "Linh", dish: "bun bo",
    occurred_on: "2026-07-21", total: 305000, participant_ids: [6, 9], created_at: "" },
  { kind: "payment", payment_id: 1, from_id: 9, to_id: 6, from_name: "Giang",
    to_name: "Linh", amount: 61000, occurred_on: "2026-07-22", created_at: "" },
] as any;

describe("TransactionTimeline", () => {
  it("renders meals and payments", () => {
    render(<TransactionTimeline events={events} />);
    expect(screen.getByText(/bun bo/)).toBeInTheDocument();
    expect(screen.getByText(/Giang/)).toBeInTheDocument();
    expect(screen.getByText(/Linh/)).toBeInTheDocument();
  });
  it("renders an empty note when there are no events", () => {
    render(<TransactionTimeline events={[]} />);
    expect(screen.getByText(/Chưa có giao dịch/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npm test -- src/components/chat/__tests__/balance-bars.test.tsx src/components/chat/__tests__/transaction-timeline.test.tsx`
Expected: FAIL — modules not found.

- [ ] **Step 3: Write minimal implementations**

Create `frontend/src/components/chat/balance-bars.tsx`:

```tsx
"use client";
import { fmt } from "@/lib/format";
import type { BalanceRow } from "@/lib/api";

const signed = (n: number) => (n > 0 ? `+${fmt(n)}` : fmt(n));

export function BalanceBars({ rows, selfId }: { rows: BalanceRow[]; selfId?: number | null }) {
  if (!rows || rows.length === 0) return null;
  const max = Math.max(1, ...rows.map((r) => Math.abs(r.balance)));
  return (
    <ul className="space-y-1.5">
      {rows.map((r) => {
        const pct = Math.round((Math.abs(r.balance) / max) * 50); // half-width each side
        const isSelf = r.id === selfId;
        return (
          <li key={r.id} className="grid grid-cols-[auto_1fr_auto] items-center gap-2 text-xs">
            <span
              data-self={isSelf || undefined}
              className={`truncate ${isSelf ? "font-semibold text-[var(--accent-text)]" : "text-[var(--text-primary)]"}`}
            >
              {r.name}
            </span>
            <span className="relative h-3 rounded-full bg-[var(--bg-base)]">
              <span className="absolute left-1/2 top-[-2px] bottom-[-2px] w-px bg-[var(--text-secondary)] opacity-50" />
              {r.balance < 0 && (
                <span className="absolute top-0 bottom-0 rounded-full bg-[#e06a4f]"
                      style={{ right: "50%", width: `${pct}%` }} />
              )}
              {r.balance > 0 && (
                <span className="absolute top-0 bottom-0 rounded-full bg-[#3f9e5a]"
                      style={{ left: "50%", width: `${pct}%` }} />
              )}
            </span>
            <span className={`tabular-nums font-semibold ${r.balance < 0 ? "text-[#c0492e]" : r.balance > 0 ? "text-[#2e7d46]" : "text-[var(--text-secondary)]"}`}>
              {signed(r.balance)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
```

Create `frontend/src/components/chat/transaction-timeline.tsx`:

```tsx
"use client";
import { fmt } from "@/lib/format";
import type { TimelineEvent } from "@/lib/api";

export function TransactionTimeline({ events }: { events: TimelineEvent[] }) {
  if (!events || events.length === 0) {
    return <p className="text-xs text-[var(--text-secondary)]">Chưa có giao dịch nào trong kỳ.</p>;
  }
  return (
    <ul className="space-y-2">
      {events.map((e) => (
        <li key={`${e.kind}-${e.kind === "meal" ? e.meal_id : e.payment_id}`}
            className="grid grid-cols-[16px_1fr_auto] items-baseline gap-2 text-xs">
          <span aria-hidden>{e.kind === "meal" ? "🍜" : "💸"}</span>
          <span className="min-w-0">
            {e.kind === "meal" ? (
              <>
                <span className="font-medium text-[var(--text-primary)]">{e.dish || "bữa ăn"}</span>
                <span className="block text-[var(--text-secondary)]">
                  {e.payer_name} trả {fmt(e.total)} đ
                </span>
              </>
            ) : (
              <span className="font-medium text-[var(--text-primary)]">
                {e.from_name} → {e.to_name}
                <span className="ml-1 font-semibold text-[var(--accent-text)]">{fmt(e.amount)} đ</span>
              </span>
            )}
          </span>
          <span className="text-[10px] text-[var(--text-secondary)]">{e.occurred_on.slice(5)}</span>
        </li>
      ))}
    </ul>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npm test -- src/components/chat/__tests__/balance-bars.test.tsx src/components/chat/__tests__/transaction-timeline.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/chat/balance-bars.tsx frontend/src/components/chat/transaction-timeline.tsx frontend/src/components/chat/__tests__/balance-bars.test.tsx frontend/src/components/chat/__tests__/transaction-timeline.test.tsx
git commit -m "feat(fe): shared BalanceBars + TransactionTimeline"
```

---

### Task 4: `StatementCard` + wire into `BotMessage`

**Files:**
- Create: `frontend/src/components/chat/statement-card.tsx`
- Modify: `frontend/src/components/chat/bot-message.tsx`
- Test: `frontend/src/components/chat/__tests__/statement-card.test.tsx`

**Interfaces:**
- Consumes: `fmt`; attachment `{type:"statement", member, period, owe:[{name,dish,amount,status,...}], owed:[…], net}`.
- Produces: `StatementCard({ attachments }: { attachments: any })`; `BotMessage` renders it when `type === "statement"`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/chat/__tests__/statement-card.test.tsx`:

```tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { BotMessage } from "../bot-message";

const att = {
  type: "statement", member: { id: 9, name: "Giang" },
  period: { from: null, to: "2026-07-22" },
  owe: [{ creditor_id: 6, name: "Linh", meal_id: 2, dish: "bun bo", occurred_on: "2026-07-21", amount: 61000, status: "unpaid" }],
  owed: [], net: -61000,
};

describe("StatementCard via BotMessage", () => {
  it("shows what you owe, the meal, and the net", () => {
    render(<BotMessage body="" attachments={att} />);
    expect(screen.getByText("Linh")).toBeInTheDocument();
    expect(screen.getByText(/bun bo/)).toBeInTheDocument();
    expect(screen.getByText(/61.000/)).toBeInTheDocument();
    expect(screen.getByText(/-61.000/)).toBeInTheDocument(); // net
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- src/components/chat/__tests__/statement-card.test.tsx`
Expected: FAIL — nothing renders for `type:"statement"`.

- [ ] **Step 3: Write minimal implementation**

Create `frontend/src/components/chat/statement-card.tsx`:

```tsx
"use client";
import { fmt } from "@/lib/format";

interface Row { name: string; dish: string | null; amount: number; status: string; }

function Section({ label, rows }: { label: string; rows: Row[] }) {
  if (!rows.length) return null;
  return (
    <div className="mt-2">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">{label}</p>
      <ul className="mt-1 divide-y divide-[var(--border)] rounded-lg border border-[var(--border)] bg-[var(--bg-base)]">
        {rows.map((r, i) => (
          <li key={i} className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
            <span className="min-w-0">
              <span className="text-[var(--text-primary)]">{r.name}</span>
              <span className="ml-2 text-xs text-[var(--text-secondary)]">
                {r.dish || "bữa ăn"}
                {r.status === "paid" && " · đã trả"}
                {r.status === "partial" && " · trả một phần"}
              </span>
            </span>
            <span className="shrink-0 font-medium text-[var(--text-secondary)]">{fmt(r.amount)} đ</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function StatementCard({ attachments }: { attachments: any }) {
  const net: number = attachments.net ?? 0;
  return (
    <div className="mt-3">
      <Section label="Bạn nợ" rows={attachments.owe ?? []} />
      <Section label="Được nợ" rows={attachments.owed ?? []} />
      <div className="mt-3 flex items-center justify-between border-t border-dashed border-[var(--border)] pt-2 text-sm">
        <span className="font-medium text-[var(--text-primary)]">Ròng</span>
        <span className={`font-semibold ${net < 0 ? "text-[#c0492e]" : net > 0 ? "text-[#2e7d46]" : "text-[var(--text-secondary)]"}`}>
          {net > 0 ? `+${fmt(net)}` : fmt(net)} đ
        </span>
      </div>
    </div>
  );
}
```

In `frontend/src/components/chat/bot-message.tsx`, import and dispatch:

```tsx
import { StatementCard } from "./statement-card";
```

Add to the render (after the `meal` line):

```tsx
      {type === "statement" && <StatementCard attachments={attachments} />}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- src/components/chat/__tests__/statement-card.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/chat/statement-card.tsx frontend/src/components/chat/bot-message.tsx frontend/src/components/chat/__tests__/statement-card.test.tsx
git commit -m "feat(fe): StatementCard (owe/owed by meal) wired into BotMessage"
```

---

### Task 5: `SummaryCard` + wire into `BotMessage`

**Files:**
- Create: `frontend/src/components/chat/summary-card.tsx`
- Modify: `frontend/src/components/chat/bot-message.tsx`
- Test: `frontend/src/components/chat/__tests__/summary-card.test.tsx`

**Interfaces:**
- Consumes: `BalanceBars`, `TransactionTimeline`; attachment `{type:"summary", period, timeline, balances}`.
- Produces: `SummaryCard({ attachments }: { attachments: any })`; `BotMessage` renders it when `type === "summary"`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/chat/__tests__/summary-card.test.tsx`:

```tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { BotMessage } from "../bot-message";

const att = {
  type: "summary", period: { from: null, to: "2026-07-22" },
  timeline: [{ kind: "meal", meal_id: 2, payer_id: 6, payer_name: "Linh", dish: "bun bo",
               occurred_on: "2026-07-21", total: 305000, participant_ids: [6], created_at: "" }],
  balances: [{ id: 6, name: "Linh", balance: 61000 }],
};

describe("SummaryCard via BotMessage", () => {
  it("renders the timeline and balance bars", () => {
    render(<BotMessage body="" attachments={att} />);
    expect(screen.getByText(/bun bo/)).toBeInTheDocument();
    expect(screen.getByText("+61.000")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- src/components/chat/__tests__/summary-card.test.tsx`
Expected: FAIL — nothing renders for `type:"summary"`.

- [ ] **Step 3: Write minimal implementation**

Create `frontend/src/components/chat/summary-card.tsx`:

```tsx
"use client";
import { BalanceBars } from "./balance-bars";
import { TransactionTimeline } from "./transaction-timeline";

export function SummaryCard({ attachments }: { attachments: any }) {
  return (
    <div className="mt-3 space-y-3">
      <div>
        <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Giao dịch</p>
        <TransactionTimeline events={attachments.timeline ?? []} />
      </div>
      {(attachments.balances ?? []).length > 0 && (
        <div className="border-t border-dashed border-[var(--border)] pt-3">
          <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Số dư</p>
          <BalanceBars rows={attachments.balances} />
        </div>
      )}
    </div>
  );
}
```

In `bot-message.tsx`, import and dispatch:

```tsx
import { SummaryCard } from "./summary-card";
```

```tsx
      {type === "summary" && <SummaryCard attachments={attachments} />}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- src/components/chat/__tests__/summary-card.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/chat/summary-card.tsx frontend/src/components/chat/bot-message.tsx frontend/src/components/chat/__tests__/summary-card.test.tsx
git commit -m "feat(fe): SummaryCard (timeline + balance bars) wired into BotMessage"
```

---

### Task 6: `LedgerPanel` (group/personal toggle, "you" highlight)

**Files:**
- Create: `frontend/src/components/chat/ledger-panel.tsx`
- Test: `frontend/src/components/chat/__tests__/ledger-panel.test.tsx`

**Interfaces:**
- Consumes: `useLedger`, `BalanceBars`, `TransactionTimeline`, `BalanceRow`/`TimelineEvent`.
- Produces: `LedgerPanel({ roomId, selfId, version }: { roomId: number; selfId: number | null; version: number })`. Group view = all balances + full timeline; "Của tôi" = self balance + timeline filtered to events involving `selfId` (meal where self is payer or in `participant_ids`; payment where self is `from_id`/`to_id`).

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/chat/__tests__/ledger-panel.test.tsx`:

```tsx
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import * as api from "@/lib/api";
import { LedgerPanel } from "../ledger-panel";

const data = {
  period: { from: null, to: "2026-07-22", keyword: "since_last" },
  balances: [
    { id: 9, name: "Giang", balance: 89000 },
    { id: 5, name: "Trang", balance: -75000 },
  ],
  timeline: [
    { kind: "meal", meal_id: 2, payer_id: 6, payer_name: "Linh", dish: "bun bo",
      occurred_on: "2026-07-21", total: 305000, participant_ids: [9], created_at: "a" },
    { kind: "payment", payment_id: 1, from_id: 5, to_id: 6, from_name: "Trang",
      to_name: "Linh", amount: 75000, occurred_on: "2026-07-22", created_at: "b" },
  ],
};

beforeEach(() => vi.spyOn(api, "getLedger").mockResolvedValue(data as any));

describe("LedgerPanel", () => {
  it("shows group balances and timeline", async () => {
    render(<LedgerPanel roomId={3} selfId={9} version={0} />);
    await waitFor(() => expect(screen.getByText("Giang")).toBeInTheDocument());
    expect(screen.getByText(/bun bo/)).toBeInTheDocument();
    expect(screen.getByText("Trang")).toBeInTheDocument();
  });

  it("filters to self on 'Của tôi'", async () => {
    render(<LedgerPanel roomId={3} selfId={9} version={0} />);
    await waitFor(() => expect(screen.getByText(/bun bo/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Của tôi/ }));
    // Giang(9) is in the meal's participants but not in the Trang->Linh payment
    expect(screen.getByText(/bun bo/)).toBeInTheDocument();
    expect(screen.queryByText(/Trang →/)).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- src/components/chat/__tests__/ledger-panel.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

Create `frontend/src/components/chat/ledger-panel.tsx`:

```tsx
"use client";
import { useState } from "react";
import { useLedger } from "@/hooks/use-ledger";
import { BalanceBars } from "./balance-bars";
import { TransactionTimeline } from "./transaction-timeline";
import type { TimelineEvent } from "@/lib/api";

function involvesSelf(e: TimelineEvent, selfId: number): boolean {
  if (e.kind === "meal") return e.payer_id === selfId || e.participant_ids.includes(selfId);
  return e.from_id === selfId || e.to_id === selfId;
}

export function LedgerPanel({ roomId, selfId, version }: { roomId: number; selfId: number | null; version: number }) {
  const { data, loading } = useLedger(roomId, version);
  const [mine, setMine] = useState(false);

  const balances = data?.balances ?? [];
  const timeline = data?.timeline ?? [];
  const shownBalances = mine && selfId != null ? balances.filter((b) => b.id === selfId) : balances;
  const shownTimeline = mine && selfId != null ? timeline.filter((e) => involvesSelf(e, selfId)) : timeline;

  return (
    <aside className="flex h-full flex-col gap-4 overflow-y-auto p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-bold text-[var(--text-primary)]">Sổ nhóm</h2>
        {selfId != null && (
          <div className="flex overflow-hidden rounded-lg border border-[var(--border)] text-xs">
            <button type="button" onClick={() => setMine(false)}
                    className={`px-2.5 py-1 ${!mine ? "bg-[var(--accent-primary)] font-semibold text-white" : "text-[var(--text-secondary)]"}`}>
              Cả nhóm
            </button>
            <button type="button" onClick={() => setMine(true)}
                    className={`px-2.5 py-1 ${mine ? "bg-[var(--accent-primary)] font-semibold text-white" : "text-[var(--text-secondary)]"}`}>
              Của tôi
            </button>
          </div>
        )}
      </div>

      {loading && !data ? (
        <p className="text-xs text-[var(--text-secondary)]">Đang tải…</p>
      ) : (
        <>
          <section>
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Số dư</p>
            <BalanceBars rows={shownBalances} selfId={selfId} />
          </section>
          <section>
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Giao dịch</p>
            <TransactionTimeline events={shownTimeline} />
          </section>
        </>
      )}
    </aside>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- src/components/chat/__tests__/ledger-panel.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/chat/ledger-panel.tsx frontend/src/components/chat/__tests__/ledger-panel.test.tsx
git commit -m "feat(fe): LedgerPanel (group/personal toggle, live via useLedger)"
```

---

### Task 7: Mount the panel in `RoomView` (desktop column + phone drawer)

**Files:**
- Modify: `frontend/src/components/chat/room-view.tsx`
- Test: `frontend/src/components/chat/__tests__/room-view-panel.test.tsx` (new)

**Interfaces:**
- Consumes: `useRoom` (now returns `ledgerVersion`), `LedgerPanel`, session `memberId`.
- Produces: a right-column panel ≥ `lg`; a "Sổ" header button toggling a slide-over drawer on smaller widths.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/chat/__tests__/room-view-panel.test.tsx`:

```tsx
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import * as api from "@/lib/api";

vi.mock("@/hooks/use-room", () => ({
  useRoom: () => ({ messages: [], typing: false, timelines: {}, activeTurn: null,
                    hasMore: false, loadingEarlier: false, loadEarlier: vi.fn(),
                    send: vi.fn(), ledgerVersion: 0 }),
  INITIAL_WINDOW_DAYS: 3,
}));
vi.mock("@/lib/session", () => ({ useSession: () => ({ memberId: 9, signOut: vi.fn() }) }));

import { RoomView } from "../room-view";

beforeEach(() => {
  vi.spyOn(api, "getMembers").mockResolvedValue([] as any);
  vi.spyOn(api, "getLedger").mockResolvedValue(
    { period: { from: null, to: "2026-07-22", keyword: "since_last" }, balances: [], timeline: [] } as any,
  );
});

describe("RoomView ledger panel", () => {
  it("has a Sổ toggle button that opens the drawer", () => {
    render(<RoomView roomId={3} />);
    const btn = screen.getByRole("button", { name: /Sổ/ });
    fireEvent.click(btn);
    // drawer renders a second 'Sổ nhóm' heading
    expect(screen.getAllByText(/Sổ nhóm/).length).toBeGreaterThan(0);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- src/components/chat/__tests__/room-view-panel.test.tsx`
Expected: FAIL — no "Sổ" button.

- [ ] **Step 3: Write minimal implementation**

In `frontend/src/components/chat/room-view.tsx`:

Add imports:

```tsx
import { LedgerPanel } from "./ledger-panel";
```

Destructure `ledgerVersion` from `useRoom` and add drawer state (inside `RoomView`, near the other hooks):

```tsx
  const { messages, typing, timelines, activeTurn, hasMore, loadingEarlier, loadEarlier, send, ledgerVersion } =
    useRoom(roomId);
  const [drawerOpen, setDrawerOpen] = useState(false);
```

Add a "Sổ" button in the header actions cluster (next to `InviteButton`):

```tsx
              <button
                type="button"
                onClick={() => setDrawerOpen(true)}
                aria-label="Sổ nhóm"
                className="shrink-0 rounded-lg border border-[var(--border)] px-2.5 py-1.5 text-sm text-[var(--text-secondary)] shadow-sm transition-colors duration-150 hover:bg-[var(--bg-base)] lg:hidden"
              >
                Sổ
              </button>
```

Wrap the existing `<main>` content so the panel is a right column on `lg`. Change the outer element from `<main className="flex h-dvh flex-col …">` to a row on `lg`:

Replace the outer `<main …>` opening tag with:

```tsx
    <div className="flex h-dvh">
      <main className="flex min-w-0 flex-1 flex-col bg-[var(--bg-base)]">
```

…and before the final closing of the component (after `</main>`’s original content, i.e. right before the `{selectedMember && …}` block), close `<main>` and add the desktop panel + mobile drawer, then close the new wrapper:

```tsx
      </main>

      {/* Desktop: persistent right column */}
      <div className="hidden w-[260px] shrink-0 border-l border-[var(--border)] bg-[var(--bg-surface)] lg:block">
        <LedgerPanel roomId={roomId} selfId={memberId} version={ledgerVersion} />
      </div>

      {/* Phone/tablet: slide-over drawer */}
      {drawerOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Sổ nhóm"
          onClick={() => setDrawerOpen(false)}
          className="fixed inset-0 z-50 bg-black/50 lg:hidden"
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="absolute right-0 top-0 h-full w-[82%] max-w-sm border-l border-[var(--border)] bg-[var(--bg-surface)] shadow-xl"
          >
            <LedgerPanel roomId={roomId} selfId={memberId} version={ledgerVersion} />
          </div>
        </div>
      )}
    </div>
```

> Note: the original `<main>` already contains header + scroll area + composer; keep all of that between the new `<main …>` open and the `</main>` you add. The `{selectedMember && …}` dialog block stays as the last child of the new outer `<div>`.

Add `Escape`-to-close for the drawer (mirrors `MemberInfoDialog`): inside `RoomView`, add:

```tsx
  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setDrawerOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen]);
```

- [ ] **Step 4: Run test + full suite**

Run: `cd frontend && npm test -- src/components/chat/__tests__/room-view-panel.test.tsx`
Expected: PASS.
Run: `cd frontend && npm test`
Expected: all green (existing + new).

- [ ] **Step 5: Manual visual check**

Follow the memory note on the PWA service worker before live-checking: unregister the SW + clear caches, then run the app (see `run-chiatienan` skill), open a room, confirm: desktop shows the right panel; narrow width shows a "Sổ" button that opens the drawer; recording a meal/payment refreshes the panel without reload.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/chat/room-view.tsx frontend/src/components/chat/__tests__/room-view-panel.test.tsx
git commit -m "feat(fe): mount LedgerPanel — desktop column + phone drawer, live via ledgerVersion"
```

---

## Self-Review

**Spec coverage (frontend slice):**
- ① personal statement UI → Task 4 (`StatementCard`). ✓
- ③ sender-scoped panel view → Task 6 ("Của tôi" filter, "you" highlight). ✓
- ④ chronological summary UI → Task 5 (`SummaryCard`) + Task 3 (`TransactionTimeline`). ✓
- ⑥ always-on panel (option A: desktop column + phone drawer, live) → Tasks 2, 6, 7. ✓
- Same period reset as chat → the panel calls `getLedger` (defaults `since_last`) and refetches on `ledger:changed`, which the backend fires on a committed settle. ✓

**Placeholder scan:** none — every component and test has complete code. The only prose instruction is the JSX-restructuring note in Task 7, which shows the exact opening/closing tags to add.

**Type consistency:** `LedgerData`/`TimelineEvent`/`BalanceRow` (Task 1) are consumed unchanged by `useLedger` (Task 2), `BalanceBars`/`TransactionTimeline` (Task 3), and `LedgerPanel` (Task 6). Attachment prop shapes in Tasks 4–5 match the backend `render_bot_attachments` output (statement: `owe/owed/net`; summary: `timeline/balances`). `ledgerVersion` returned by `useRoom` (Task 2) is the `version` prop threaded into `LedgerPanel` (Tasks 6–7). Event `kind` discriminants (`"meal"`/`"payment"`) and field names (`payer_name`, `from_name`, `to_name`, `participant_ids`) match the backend timeline (backend Tasks 2, 5, 9).

**Dependency on backend:** Tasks 4–5 need backend Task 7 (attachment mapping); Task 6–7 need backend Tasks 9–10 (endpoint + `ledger:changed`). Land the backend plan first (or at least its Tasks 7, 9, 10) before frontend Tasks 4–7.
