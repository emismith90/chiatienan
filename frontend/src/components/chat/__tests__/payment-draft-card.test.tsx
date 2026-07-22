import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { PaymentDraftCard } from "../payment-draft-card";

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  commitDraft: vi.fn(() => Promise.resolve({})),
  cancelDraft: vi.fn(() => Promise.resolve({})),
}));
import * as api from "@/lib/api";

const members = [
  { id: 1, display_name: "Alice" },
  { id: 2, display_name: "Bob" },
  { id: 3, display_name: "Carol" },
];

const msg = (status: string) => ({
  id: 9,
  kind: "payment_draft",
  attachments: {
    type: "payment_draft",
    status,
    transfers: [
      { from_member_id: 1, to_member_id: 3, amount: 50000, note: null },
      { from_member_id: 2, to_member_id: 3, amount: 30000, note: null },
    ],
  },
});

describe("PaymentDraftCard", () => {
  it("renders one row per transfer with payer, payee and amount", () => {
    render(<PaymentDraftCard message={msg("pending")} members={members} roomId={3} />);
    // Both payers and the shared payee appear.
    expect(screen.getByText(/Alice/)).toBeInTheDocument();
    expect(screen.getByText(/Bob/)).toBeInTheDocument();
    expect(screen.getAllByText(/Carol/).length).toBe(2);
    // Both amounts render (vi-VN groups with '.').
    expect(screen.getByText(/50\.000/)).toBeInTheDocument();
    expect(screen.getByText(/30\.000/)).toBeInTheDocument();
  });

  it("confirms via commitDraft(roomId, messageId)", () => {
    render(<PaymentDraftCard message={msg("pending")} members={members} roomId={3} />);
    fireEvent.click(screen.getByRole("button", { name: /confirm/i }));
    expect(api.commitDraft).toHaveBeenCalledWith(3, 9);
  });

  it("cancels via cancelDraft(roomId, messageId)", () => {
    render(<PaymentDraftCard message={msg("pending")} members={members} roomId={3} />);
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(api.cancelDraft).toHaveBeenCalledWith(3, 9);
  });

  it("hides actions and shows Recorded once committed", () => {
    render(<PaymentDraftCard message={msg("committed")} members={members} roomId={3} />);
    expect(screen.queryByRole("button", { name: /confirm/i })).not.toBeInTheDocument();
    expect(screen.getByText(/Recorded/)).toBeInTheDocument();
  });
});
