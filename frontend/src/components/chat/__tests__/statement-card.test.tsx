import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import * as api from "@/lib/api";
import { BotMessage } from "../bot-message";

const att = {
  type: "statement", member: { id: 9, name: "Giang" },
  period: { from: null, to: "2026-07-22" },
  owe: [{ creditor_id: 6, name: "Linh", meal_id: 2, dish: "bun bo", occurred_on: "2026-07-21", amount: 61000, status: "unpaid" }],
  owed: [], net: -61000,
};

beforeEach(() => vi.restoreAllMocks());

describe("StatementCard via BotMessage", () => {
  it("shows what you owe, the meal, and the net", () => {
    render(<BotMessage body="" attachments={att} roomId={3} />);
    expect(screen.getByText("Linh")).toBeInTheDocument();
    expect(screen.getByText(/bun bo/)).toBeInTheDocument();
    expect(screen.getByText(/-61.000/)).toBeInTheDocument(); // net
  });

  it("Đã trả records the meal and flips the row", async () => {
    const spy = vi.spyOn(api, "quickPay").mockResolvedValue({ ok: true, payment_id: 1, amount: 61000 });
    render(<BotMessage body="" attachments={att} roomId={3} />);
    fireEvent.click(screen.getByRole("button", { name: /Đã trả/ }));
    expect(spy).toHaveBeenCalledWith(3, 6, 2);
    await waitFor(() => expect(screen.getByText(/đã trả/)).toBeInTheDocument());
  });
});
