import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import * as api from "@/lib/api";
import { LedgerPanel } from "../ledger-panel";

const data = {
  period: { from: null, to: "2026-07-22", keyword: "since_last" },
  balances: [
    { id: 9, name: "Giang", balance: -61000 },
    { id: 6, name: "Linh", balance: 61000 },
  ],
  timeline: [
    { kind: "meal", meal_id: 2, payer_id: 6, payer_name: "Linh", dish: "bun bo",
      occurred_on: "2026-07-21", total: 122000, participant_ids: [9], created_at: "a" },
  ],
  me: {
    owe: [{ other_id: 6, name: "Linh", meal_id: 2, dish: "bun bo",
            occurred_on: "2026-07-21", amount: 61000, status: "unpaid" }],
    owed: [], net: -61000,
  },
};

beforeEach(() => {
  vi.spyOn(api, "getLedger").mockResolvedValue(data as any);
  vi.spyOn(api, "quickPay").mockResolvedValue({ ok: true, payment_id: 1, amount: 61000 });
});

describe("LedgerPanel", () => {
  it("shows group balances and timeline", async () => {
    render(<LedgerPanel roomId={3} selfId={9} version={0} />);
    await waitFor(() => expect(screen.getByText("Giang")).toBeInTheDocument());
    expect(screen.getByText(/bun bo/)).toBeInTheDocument();
    expect(screen.getByText("Linh")).toBeInTheDocument();
  });

  it("shows the caller's statement with a 'Mark paid' button on 'Mine'", async () => {
    render(<LedgerPanel roomId={3} selfId={9} version={0} />);
    await waitFor(() => expect(screen.getByText(/bun bo/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Mine/ }));
    const pay = screen.getByRole("button", { name: /Mark paid/ });
    fireEvent.click(pay);
    expect(api.quickPay).toHaveBeenCalledWith(3, 6, 2);
  });

  it("does not throw on 'Mine' when the ledger has no `me` field", async () => {
    vi.spyOn(api, "getLedger").mockResolvedValue({
      period: data.period,
      balances: data.balances,
      timeline: data.timeline,
    } as any);
    render(<LedgerPanel roomId={3} selfId={9} version={0} />);
    await waitFor(() => expect(screen.getByText("Giang")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Mine/ }));
    // The "Balances" section must still render without throwing on the missing `me`.
    expect(screen.getByText("Balances")).toBeInTheDocument();
  });
});
