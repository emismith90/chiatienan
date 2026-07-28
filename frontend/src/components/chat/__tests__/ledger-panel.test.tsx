import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import * as api from "@/lib/api";
import { LedgerPanel } from "../ledger-panel";

const data = {
  period: { from: null, to: "2026-07-22", keyword: "since_last" },
  outstanding: [
    { debtor_id: 9, debtor_name: "Giang", creditor_id: 6, creditor_name: "Linh", amount: 61000 },
  ],
  timeline: [
    { kind: "meal", meal_id: 2, payer_id: 6, payer_name: "Linh", dish: "bun bo",
      occurred_on: "2026-07-21", total: 122000, participant_ids: [9], created_at: "a" },
  ],
  me: {
    owe: [{ other_id: 6, name: "Linh", meal_id: 2, dish: "bun bo",
            occurred_on: "2026-07-21", amount: 61000, status: "unpaid" }],
    owed: [],
  },
};

beforeEach(() => {
  vi.spyOn(api, "getLedger").mockResolvedValue(data as any);
  vi.spyOn(api, "quickPay").mockResolvedValue({ ok: true, payment_id: 1, amount: 61000 });
});

describe("LedgerPanel", () => {
  it("opens on the caller's own statement, with a 'Mark paid' button", async () => {
    render(<LedgerPanel roomId={3} selfId={9} version={0} />);
    // No tab click: Mine is the default view.
    await waitFor(() => expect(screen.getByText("You owe")).toBeInTheDocument());
    expect(screen.getByText("Linh")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Mark paid/ }));
    expect(api.quickPay).toHaveBeenCalledWith(3, 6, 2);
  });

  it("shows who owes whom and the timeline on 'Group'", async () => {
    render(<LedgerPanel roomId={3} selfId={9} version={0} />);
    await waitFor(() => expect(screen.getByText("You owe")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Group/ }));
    expect(screen.getByText("Ai nợ ai")).toBeInTheDocument();
    expect(screen.getByText("Giang")).toBeInTheDocument();
    expect(screen.getByText(/bun bo/)).toBeInTheDocument();
  });

  it("reports no net figure anywhere, in either view", async () => {
    render(<LedgerPanel roomId={3} selfId={9} version={0} />);
    await waitFor(() => expect(screen.getByText("You owe")).toBeInTheDocument());
    expect(screen.queryByText("Net")).not.toBeInTheDocument();
    expect(screen.queryByText(/-61\.000/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Group/ }));
    expect(screen.queryByText("Net")).not.toBeInTheDocument();
    expect(screen.queryByText(/-61\.000/)).not.toBeInTheDocument();
  });

  it("falls back to the group view when there is no signed-in member", async () => {
    render(<LedgerPanel roomId={3} selfId={null} version={0} />);
    await waitFor(() => expect(screen.getByText("Ai nợ ai")).toBeInTheDocument());
    // No toggle to offer: "mine" has no meaning without a member id.
    expect(screen.queryByRole("button", { name: /Mine/ })).not.toBeInTheDocument();
  });

  it("does not throw on 'Mine' when the ledger has no `me` field", async () => {
    vi.spyOn(api, "getLedger").mockResolvedValue({
      period: data.period,
      outstanding: data.outstanding,
      timeline: data.timeline,
    } as any);
    render(<LedgerPanel roomId={3} selfId={9} version={0} />);
    await waitFor(() =>
      expect(screen.getByText(/không nợ ai, không ai nợ bạn/)).toBeInTheDocument());
  });
});
