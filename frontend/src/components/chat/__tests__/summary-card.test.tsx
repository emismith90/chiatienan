import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { BotMessage } from "../bot-message";

const att = {
  type: "summary", period: { from: null, to: "2026-07-22" },
  timeline: [{ kind: "meal", meal_id: 2, payer_id: 6, payer_name: "Linh", dish: "bun bo",
               occurred_on: "2026-07-21", total: 305000, participant_ids: [6], created_at: "" }],
  outstanding: [{ debtor_id: 9, debtor_name: "Giang", creditor_id: 6,
                  creditor_name: "Linh", amount: 61000 }],
};

describe("SummaryCard via BotMessage", () => {
  it("renders the timeline and who owes whom, with no net column", () => {
    render(<BotMessage body="" attachments={att} roomId={3} />);
    expect(screen.getByText(/bun bo/)).toBeInTheDocument();
    expect(screen.getByText("Ai nợ ai")).toBeInTheDocument();
    expect(screen.getByText("Giang")).toBeInTheDocument();
    expect(screen.getByText(/61\.000/)).toBeInTheDocument();
    expect(screen.queryByText("+61.000")).not.toBeInTheDocument();
  });
});

describe("opening the ledger from a history answer", () => {
  it("offers the summary's own period and reports it back", async () => {
    const { fireEvent } = await import("@testing-library/react");
    const onOpenLedger = vi.fn();
    render(
      <BotMessage
        body="Tóm tắt 2026-07-20 → 2026-07-26: 2 bữa, 2 lượt trả tiền trong 3 ngày — chi tiết ở dưới."
        attachments={{ type: "summary", period: { from: "2026-07-20", to: "2026-07-26" },
                       timeline: [], outstanding: [] }}
        roomId={3}
        onOpenLedger={onOpenLedger}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Mở sổ 2026-07-20 → 2026-07-26/ }));
    expect(onOpenLedger).toHaveBeenCalledWith({ from: "2026-07-20", to: "2026-07-26" });
  });

  it("has no button when nothing can consume it", () => {
    render(<BotMessage body="Tóm tắt" roomId={3}
                       attachments={{ type: "summary", period: { from: null, to: "2026-07-26" }, timeline: [] }} />);
    expect(screen.queryByRole("button", { name: /Mở sổ/ })).not.toBeInTheDocument();
  });
});
