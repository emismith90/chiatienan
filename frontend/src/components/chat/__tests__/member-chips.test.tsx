import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import * as api from "@/lib/api";

vi.mock("@/hooks/use-room", () => ({
  useRoom: () => ({ messages: [], typing: false, timelines: {}, activeTurn: null,
                    hasMore: false, loadingEarlier: false, loadEarlier: vi.fn(),
                    send: vi.fn(), ledgerVersion: 0 }),
  INITIAL_WINDOW_DAYS: 3,
}));
vi.mock("@/lib/session", () => ({
  useSession: () => ({
    memberId: 4, signOut: vi.fn(),
    rooms: [], roomId: 3, roomName: "12B +2 🐔", switchRoom: vi.fn(), removeRoom: vi.fn(),
  }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { RoomView } from "../room-view";

/** Room 3's real roster: seven members, two of them flagged out of default
 * group activities. */
const MEMBERS = [
  { id: 11, display_name: "Duc Le", nickname: "Duc Le", default_participant: false },
  { id: 4, display_name: "Emi", nickname: "Emi", default_participant: true },
  { id: 9, display_name: "Giang Hoàng", nickname: "Giang", default_participant: true },
  { id: 7, display_name: "Kun", nickname: "Kun", default_participant: false },
  { id: 6, display_name: "Linh Nguyen", nickname: "Linh", default_participant: true },
  { id: 8, display_name: "Nhím", nickname: "Nhím", default_participant: true },
  { id: 5, display_name: "Tabu", nickname: "Tabu", default_participant: true },
];

beforeEach(() => {
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: false, media: q, addEventListener() {}, removeEventListener() {},
  }));
  Element.prototype.scrollIntoView = vi.fn();
  vi.spyOn(api, "getMembers").mockResolvedValue(MEMBERS as any);
  vi.spyOn(api, "getLedger").mockResolvedValue(
    { period: { from: null, to: "2026-08-14", keyword: "since_last" }, outstanding: [], timeline: [],
      me: { owe: [], owed: [] } } as any,
  );
});

describe("MemberChips collapse", () => {
  it("summarises the roster behind a toggle that starts closed", async () => {
    render(<RoomView roomId={3} />);
    const toggle = await screen.findByRole("button", { name: /7 members/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    // Collapsed is a CSS state, not an unmounted subtree: the chips stay in the
    // server-rendered HTML so a desktop first paint shows them (`hidden lg:flex`).
    const grid = document.getElementById("member-chips")!;
    expect(grid.className).toContain("hidden");
    expect(grid.className).toContain("lg:flex");

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(document.getElementById("member-chips")!.className).not.toContain("hidden");
  });

  it("keeps every member tappable once expanded", async () => {
    render(<RoomView roomId={3} />);
    fireEvent.click(await screen.findByRole("button", { name: /7 members/ }));
    for (const m of MEMBERS) {
      expect(screen.getByRole("button", { name: new RegExp(m.nickname) })).toBeInTheDocument();
    }
  });

  it("marks the members that 'everyone' splits leave out", async () => {
    render(<RoomView roomId={3} />);
    fireEvent.click(await screen.findByRole("button", { name: /7 members/ }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Duc Le/ })).toHaveAttribute(
        "title",
        expect.stringContaining("not included by default in group splits"),
      ),
    );
    // The opted-in majority carries no such warning.
    expect(screen.getByRole("button", { name: /Nhím/ }).getAttribute("title")).not.toContain(
      "not included",
    );
  });
});
