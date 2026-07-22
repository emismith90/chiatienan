import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import * as api from "@/lib/api";

vi.mock("@/hooks/use-room", () => ({
  useRoom: () => ({ messages: [], typing: false, timelines: {}, activeTurn: null,
                    hasMore: false, loadingEarlier: false, loadEarlier: vi.fn(),
                    send: vi.fn(), ledgerVersion: 0 }),
  INITIAL_WINDOW_DAYS: 3,
}));
// RoomView's subtree (RoomSwitcher) also reads room fields off the session.
vi.mock("@/lib/session", () => ({
  useSession: () => ({
    memberId: 9, signOut: vi.fn(),
    rooms: [], roomId: 3, roomName: "Lunch", switchRoom: vi.fn(), removeRoom: vi.fn(),
  }),
}));
// RoomView renders RoomSwitcher, which reads next/navigation's useRouter.
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { RoomView } from "../room-view";

beforeEach(() => {
  // jsdom lacks matchMedia; RoomView's header renders ThemeToggle which reads it.
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: false, media: q, addEventListener() {}, removeEventListener() {},
  }));
  // jsdom lacks scrollIntoView; RoomView auto-scrolls to the newest message.
  Element.prototype.scrollIntoView = vi.fn();
  vi.spyOn(api, "getMembers").mockResolvedValue([] as any);
  vi.spyOn(api, "getLedger").mockResolvedValue(
    { period: { from: null, to: "2026-07-22", keyword: "since_last" }, balances: [], timeline: [],
      me: { owe: [], owed: [], net: 0 } } as any,
  );
});

describe("RoomView ledger panel", () => {
  it("has a Sổ toggle button that opens the drawer", () => {
    render(<RoomView roomId={3} />);
    const btn = screen.getByRole("button", { name: /Sổ/ });
    // Drawer is closed before the click (desktop LedgerPanel column is not
    // the drawer dialog, so scoping by role+name proves the toggle works).
    expect(screen.queryByRole("dialog", { name: "Sổ nhóm" })).toBeNull();
    fireEvent.click(btn);
    // Clicking the Sổ button opens the slide-over drawer dialog.
    expect(screen.getByRole("dialog", { name: "Sổ nhóm" })).toBeInTheDocument();
  });
});
