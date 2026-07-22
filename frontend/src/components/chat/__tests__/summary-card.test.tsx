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
    render(<BotMessage body="" attachments={att} roomId={3} />);
    expect(screen.getByText(/bun bo/)).toBeInTheDocument();
    expect(screen.getByText("+61.000")).toBeInTheDocument();
  });
});
