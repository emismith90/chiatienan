import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MessageList } from "../message-list";

describe("MessageList context_reset divider", () => {
  it("renders a context_reset message as a centered divider showing its body", () => {
    const messages = [
      { id: 1, kind: "text", body: "hello", author: { id: 5, name: "An" } },
      { id: 2, kind: "context_reset", body: "🧹 Đã xoá ngữ cảnh" },
    ];
    render(<MessageList messages={messages as any} members={[]} roomId={1} />);

    // the divider body text is present
    const divider = screen.getByText("🧹 Đã xoá ngữ cảnh");
    expect(divider).toBeInTheDocument();

    // the divider is rendered in a centered container (not as a right-aligned human bubble)
    expect(divider.closest(".justify-center")).toBeInTheDocument();

    // the human message still renders
    const humanMessage = screen.getByText("hello");
    expect(humanMessage).toBeInTheDocument();

    // the human message is NOT in the centered divider container (it's right-aligned)
    expect(humanMessage.closest(".justify-center")).not.toBeInTheDocument();
  });
});

describe("MessageList unknown draft kinds", () => {
  it("renders a kind it has no bespoke card for through the generic DraftCard", () => {
    // A poker `game_draft`: body is empty and everything is in attachments, so
    // before the fallback this fell through to HumanMessage as a blank bubble
    // with no way to confirm it.
    const messages = [
      {
        id: 3,
        kind: "game_draft",
        body: "",
        attachments: { type: "game_draft", status: "pending", pot: 2_500_000, players: 3 },
      },
    ];
    render(<MessageList messages={messages as any} members={[]} roomId={1} />);

    expect(screen.getByText("Game")).toBeInTheDocument();
    expect(screen.getByText("2.500.000")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /confirm/i })).toBeInTheDocument();
    // It is Phoenix speaking, on the left — not a human bubble.
    expect(screen.getByText("Phoenix").closest(".items-start")).toBeInTheDocument();
  });

  it("still routes the kinds that have their own card to that card", () => {
    // The generic branch must sit BELOW the bespoke ones: a memo card has no
    // Confirm button (it says Remember), and the money cards name their members.
    const messages = [
      {
        id: 4,
        kind: "memo_draft",
        body: "",
        attachments: { type: "memo_draft", status: "pending", action: "add",
                       subject: "place:pho-hanh", text: "Đóng cửa thứ hai." },
      },
      {
        id: 5,
        kind: "payment_draft",
        body: "",
        attachments: { type: "payment_draft", status: "pending",
                       transfers: [{ from_member_id: 1, to_member_id: 2, amount: 50_000, note: null }] },
      },
    ];
    render(<MessageList messages={messages as any} members={[{ id: 1, display_name: "Alice" },
                                                             { id: 2, display_name: "Bob" }]} roomId={1} />);

    expect(screen.getByRole("button", { name: /remember/i })).toBeInTheDocument();
    expect(screen.getByText("Đóng cửa thứ hai.")).toBeInTheDocument();
    expect(screen.getByText(/Alice/)).toBeInTheDocument();
    // Neither card is the generic one, which would title them "Memo" / "Payment"
    // and list raw fields like "Subject".
    expect(screen.queryByText("Subject")).not.toBeInTheDocument();
  });
});
