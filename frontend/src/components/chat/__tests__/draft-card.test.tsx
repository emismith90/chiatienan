import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { DraftCard, draftTitle } from "../draft-card";

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  commitDraft: vi.fn(() => Promise.resolve({})),
  cancelDraft: vi.fn(() => Promise.resolve({})),
}));
import * as api from "@/lib/api";

/** A poker `game_draft` as the backend publishes it: body "", the payload in
 *  attachments, no frontend knowledge of the kind. */
const game = (status: string) => ({
  id: 11,
  kind: "game_draft",
  body: "",
  attachments: {
    type: "game_draft",
    status,
    turn_id: "t-1",
    raw_input: "poker tối qua",
    played_on: "2026-09-05",
    house: 0,
    note: null,
    pot: 2_500_000,
    players: 3,
    entries: [
      { member: 1, buy_in: 1_000_000, cash_out: 1_400_000 },
      { member: 2, buy_in: 1_000_000, cash_out: 600_000 },
      { member: 3, buy_in: 500_000, cash_out: 500_000 },
    ],
    // `prepare()` adds these on every create and edit, so a real card has them.
    nets: [
      { member: 1, name: "Alice", net: 400_000 },
      { member: 2, name: "Bob", net: -400_000 },
      { member: 3, name: "Carol", net: 0 },
    ],
    edges_preview: [{ from_member_id: 2, to_member_id: 1, amount: 400_000 }],
  },
});

describe("DraftCard (generic fallback)", () => {
  it("titles the card from the kind", () => {
    expect(draftTitle("game_draft")).toBe("Game");
    expect(draftTitle("night_out_draft")).toBe("Night out");
    expect(draftTitle(undefined)).toBe("Draft");
  });

  it("summarises the payload's scalar fields", () => {
    render(<DraftCard message={game("pending")} roomId={3} />);
    expect(screen.getByText("Game")).toBeInTheDocument();
    expect(screen.getByText("Played on")).toBeInTheDocument();
    expect(screen.getByText("2026-09-05")).toBeInTheDocument();
    // Numbers are grouped vi-VN, like every other money card.
    expect(screen.getByText("2.500.000")).toBeInTheDocument();
  });

  it("spells out every row of a list of objects, not just how many there are", () => {
    // The whole point of the card is that a person checks the claim before it
    // becomes ledger rows: a game writes debt edges between named members, so
    // "Entries: 3 items" would be a Confirm button over numbers nobody saw.
    render(<DraftCard message={game("pending")} roomId={3} />);
    expect(screen.getByText("Member 1 · Buy in 1.000.000 · Cash out 1.400.000")).toBeInTheDocument();
    expect(screen.getByText("Member 2 · Buy in 1.000.000 · Cash out 600.000")).toBeInTheDocument();
    expect(screen.getByText("Member 1 · Name Alice · Net 400.000")).toBeInTheDocument();
    // Ids are numbers but not quantities — grouping one would read as money.
    expect(screen.getByText("From member id 2 · To member id 1 · Amount 400.000")).toBeInTheDocument();
  });

  it("hides the kernel's bookkeeping and empty fields", () => {
    render(<DraftCard message={game("pending")} roomId={3} />);
    // turn_id / raw_input / status / type are plumbing, not a proposal; `note`
    // is null and `house` 0 is a real number worth showing.
    expect(screen.queryByText("Turn id")).not.toBeInTheDocument();
    expect(screen.queryByText("Raw input")).not.toBeInTheDocument();
    expect(screen.queryByText("Type")).not.toBeInTheDocument();
    expect(screen.queryByText("Note")).not.toBeInTheDocument();
    expect(screen.getByText("House")).toBeInTheDocument();
  });

  it("shows the message body when the card carries one", () => {
    const m = { ...game("pending"), body: "Ván tối qua, chốt nhé?" };
    render(<DraftCard message={m} roomId={3} />);
    expect(screen.getByText("Ván tối qua, chốt nhé?")).toBeInTheDocument();
  });

  it("confirms through the generic commit route", () => {
    render(<DraftCard message={game("pending")} roomId={3} />);
    fireEvent.click(screen.getByRole("button", { name: /confirm/i }));
    expect(api.commitDraft).toHaveBeenCalledWith(3, 11);
  });

  it("cancels through the generic draft route", () => {
    render(<DraftCard message={game("pending")} roomId={3} />);
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(api.cancelDraft).toHaveBeenCalledWith(3, 11);
  });

  it("drops the buttons and labels the outcome once it is no longer pending", () => {
    const { unmount } = render(<DraftCard message={game("committed")} roomId={3} />);
    expect(screen.queryByRole("button", { name: /confirm/i })).not.toBeInTheDocument();
    expect(screen.getByText("Recorded")).toBeInTheDocument();
    unmount();

    render(<DraftCard message={game("superseded")} roomId={3} />);
    expect(screen.getByText("Replaced by a newer proposal")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /confirm/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /cancel/i })).not.toBeInTheDocument();
  });
});
