import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { perHead, ExpenseDraftCard } from "../expense-draft-card";

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  patchDraft: vi.fn(() => Promise.resolve()),
  commitDraft: vi.fn(() => Promise.resolve()),
  cancelDraft: vi.fn(() => Promise.resolve()),
  recommitDraft: vi.fn(() => Promise.resolve({ ok: true, meal_id: 9 })),
}));
import * as api from "@/lib/api";

const members = [{ id: 1, display_name: "A" }, { id: 2, display_name: "B" }];
const committed = {
  id: 50,
  attachments: {
    type: "expense_draft", status: "committed", committed_meal_id: 9,
    payer_member_id: 1, member_participants: [1, 2], guests: [],
    bill_total: 300, adjustments: [],
  },
};

describe("ExpenseDraftCard edit-when-committed", () => {
  it("shows an Edit button on a committed card", () => {
    render(<ExpenseDraftCard message={committed} members={members} roomId={1} />);
    expect(screen.getByRole("button", { name: /edit/i })).toBeInTheDocument();
  });

  it("editing re-enables fields and Save calls recommitDraft", () => {
    render(<ExpenseDraftCard message={committed} members={members} roomId={1} />);
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    const total = screen.getByLabelText(/bill total/i) as HTMLInputElement;
    expect(total.disabled).toBe(false);
    fireEvent.change(total, { target: { value: "600" } });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    expect(api.recommitDraft).toHaveBeenCalledWith(1, 50, expect.objectContaining({ bill_total: 600 }));
  });

  it("Cancel edit reverts field changes back to the original values", () => {
    render(<ExpenseDraftCard message={committed} members={members} roomId={1} />);
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    const total = screen.getByLabelText(/bill total/i) as HTMLInputElement;
    fireEvent.change(total, { target: { value: "600" } });
    expect(total.value).toBe("600");
    fireEvent.click(screen.getByRole("button", { name: /cancel edit/i }));

    // Re-entering Edit should show the original value, not the discarded one.
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    const totalAgain = screen.getByLabelText(/bill total/i) as HTMLInputElement;
    expect(totalAgain.value).toBe(String(committed.attachments.bill_total));
  });
});

describe("perHead", () => {
  it("splits the total evenly across billed members and guests", () => {
    expect(perHead(400_000, 3, 1)).toBe(100_000);
  });

  it("floors the result when the split is not even", () => {
    expect(perHead(100, 3, 0)).toBe(33);
  });

  it("returns 0 when there are no heads to bill (avoids divide-by-zero)", () => {
    expect(perHead(400_000, 0, 0)).toBe(0);
  });

  it("counts guests toward the head count", () => {
    expect(perHead(300_000, 2, 1)).toBe(perHead(300_000, 3, 0));
  });

  it("subtracts the adjustments total before dividing, matching the server's base", () => {
    // total 400_000, 4 heads, one member adjusted +50_000 -> base = (400_000 - 50_000) / 4
    expect(perHead(400_000, 4, 0, 50_000)).toBe(87_500);
  });

  it("defaults the adjustments total to 0 when omitted (back-compat)", () => {
    expect(perHead(400_000, 4, 0)).toBe(perHead(400_000, 4, 0, 0));
  });
});
