import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { prorate, ExpenseDraftCard } from "../expense-draft-card";
import { fmt } from "@/lib/format";

vi.mock("@/lib/api", () => ({
  // Mirrors the real signature (status, msg) — a one-arg stub swallowed the
  // message and the card fell back to its generic error text.
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, msg: string) {
      super(msg);
      this.status = status;
      this.name = "ApiError";
    }
  },
  patchDraft: vi.fn(() => Promise.resolve()),
  commitDraft: vi.fn(() => Promise.resolve()),
  cancelDraft: vi.fn(() => Promise.resolve()),
  recommitDraft: vi.fn(() => Promise.resolve({ ok: true, meal_id: 9 })),
}));
import * as api from "@/lib/api";

const members = [
  { id: 1, display_name: "Emi" }, { id: 2, display_name: "Nhím" },
  { id: 3, display_name: "Giang" }, { id: 4, display_name: "Linh" },
  { id: 5, display_name: "Kun" }, { id: 6, display_name: "Tabu" },
];

const PAID = 324_200;
const PRICES = [69_500, 69_500, 94_200, 69_000, 69_000, 42_000];
const items = members.map((m, i) => ({ member: m.id, amount: PRICES[i] }));

const itemized = {
  id: 70,
  attachments: {
    type: "expense_draft", status: "pending",
    payer_member_id: 1, member_participants: [1, 2, 3, 4, 5, 6], guests: [],
    bill_total: PAID, adjustments: [], items,
  },
};

describe("prorate equal mode (mirrors money._equal_delta_shares)", () => {
  it("takes the same amount off everyone, exactly summing to total", () => {
    // Production: Σ 414,200 paid 324,200 -> 15,000 off each of six.
    const items = [
      { member: 4, amount: 69_500 }, { member: 8, amount: 69_500 },
      { member: 9, amount: 94_200 }, { member: 6, amount: 69_500 },
      { member: 7, amount: 69_500 }, { member: 5, amount: 42_000 },
    ];
    const shares = prorate(324_200, items, "equal");
    expect(shares.get(5)).toBe(27_000);
    expect(shares.get(9)).toBe(79_200);
    expect([...shares.values()].reduce((a, b) => a + b, 0)).toBe(324_200);
  });

  it("disagrees with proportional, as the server does", () => {
    const items = [{ member: 1, amount: 100_000 }, { member: 2, amount: 20_000 }];
    expect(prorate(100_000, items, "equal").get(2)).toBe(10_000);
    expect(prorate(100_000, items, "proportional").get(2)).toBe(16_667);
  });

  it("spreads a fee equally too", () => {
    const items = [{ member: 1, amount: 50_000 }, { member: 2, amount: 40_000 }];
    const shares = prorate(100_000, items, "equal");
    expect(shares.get(1)).toBe(55_000);
    expect(shares.get(2)).toBe(45_000);
  });
});

describe("prorate (mirrors money.prorate_items)", () => {
  it("scales the discount across the items and still sums to the bill", () => {
    const shares = prorate(PAID, items);
    expect([...shares.values()].reduce((a, b) => a + b, 0)).toBe(PAID);
    // The 89.000đ promo lands proportionally, not on one person.
    members.forEach((m, i) => {
      expect(shares.get(m.id)!).toBeLessThan(PRICES[i]);
    });
    expect(shares.get(4)).toBe(shares.get(5)); // the shared "2× cơm tấm" line
  });

  it("scales a fee upward the same way", () => {
    const shares = prorate(500_000, [
      { member: 1, amount: 100_000 }, { member: 2, amount: 300_000 },
    ]);
    expect(shares.get(1)).toBe(125_000);
    expect(shares.get(2)).toBe(375_000);
  });

  it("gives the leftover đồng to the largest remainder, ties by member id", () => {
    const shares = prorate(10, [
      { member: 9, amount: 1 }, { member: 5, amount: 1 }, { member: 7, amount: 1 },
    ]);
    expect([...shares.values()].reduce((a, b) => a + b, 0)).toBe(10);
    expect(shares.get(5)).toBe(4);
  });

  it("is safe on degenerate input instead of producing NaN", () => {
    expect([...prorate(100, []).values()]).toEqual([]);
    expect(prorate(100, [{ member: 1, amount: 0 }]).get(1)).toBe(0);
    expect(prorate(0, [{ member: 1, amount: 5 }]).get(1)).toBe(0);
  });
});

describe("ExpenseDraftCard itemized mode", () => {
  it("shows each person's bill price and what they actually pay", () => {
    render(<ExpenseDraftCard message={itemized} members={members} roomId={1} />);
    const emi = screen.getByLabelText(/item price for Emi/i) as HTMLInputElement;
    expect(emi.value).toBe("69500");
    // Prorated down from list price, and shown on the card (Giang's mỳ cua is
    // the one unique price on this bill).
    const share = prorate(PAID, items).get(3)!;
    expect(share).toBeLessThan(94_200);
    expect(screen.getByText(`${fmt(share)}đ`)).toBeInTheDocument();
  });

  it("explains the gap between the item prices and the bill", () => {
    render(<ExpenseDraftCard message={itemized} members={members} roomId={1} />);
    expect(screen.getByText(/discount/i)).toBeInTheDocument();
    expect(screen.getByText(/below the item prices/i)).toBeInTheDocument();
  });

  it("hides the per-person estimate that would misdescribe an itemized split", () => {
    render(<ExpenseDraftCard message={itemized} members={members} roomId={1} />);
    expect(screen.queryByText(/đ\/person/)).toBeNull();
  });

  it("patches items so the server can re-prorate", async () => {
    vi.useFakeTimers();
    render(<ExpenseDraftCard message={itemized} members={members} roomId={1} />);
    fireEvent.change(screen.getByLabelText(/item price for Tabu/i), { target: { value: "50000" } });
    await vi.advanceTimersByTimeAsync(700);
    expect(api.patchDraft).toHaveBeenCalledWith(1, 70, expect.objectContaining({
      items: expect.arrayContaining([{ member: 6, amount: 50000 }]),
    }));
    vi.useRealTimers();
  });

  it("dropping a diner drops their item too, so the two never drift apart", async () => {
    vi.useFakeTimers();
    render(<ExpenseDraftCard message={itemized} members={members} roomId={1} />);
    fireEvent.click(screen.getByRole("button", { name: "Tabu" }));
    await vi.advanceTimersByTimeAsync(700);
    const patch = (api.patchDraft as any).mock.calls.at(-1)[2];
    expect(patch.member_participants).not.toContain(6);
    expect(patch.items.map((i: any) => i.member)).not.toContain(6);
    vi.useRealTimers();
  });

  it("switching an even-split draft to per-item seeds prices from the estimate", () => {
    const even = {
      id: 71,
      attachments: {
        type: "expense_draft", status: "pending", payer_member_id: 1,
        member_participants: [1, 2], guests: [], bill_total: 300, adjustments: [],
      },
    };
    render(<ExpenseDraftCard message={even} members={members.slice(0, 2)} roomId={1} />);
    expect(screen.getByText(/150 đ\/person/)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/split by item/i));
    expect((screen.getByLabelText(/item price for Emi/i) as HTMLInputElement).value).toBe("150");
  });

  it("offers no per-item mode while a guest is on the draft", () => {
    const withGuest = {
      id: 72,
      attachments: {
        type: "expense_draft", status: "pending", payer_member_id: 1,
        member_participants: [1, 2], guests: ["Khách"], bill_total: 300, adjustments: [],
      },
    };
    render(<ExpenseDraftCard message={withGuest} members={members.slice(0, 2)} roomId={1} />);
    expect(screen.queryByLabelText(/split by item/i)).toBeNull();
  });
});

describe("Record now flushes pending edits first", () => {
  it("patches the current card state before committing", async () => {
    // commit_draft records what is STORED, and the PATCH is 600ms behind — so
    // confirming right after an edit recorded the previous total while the card
    // showed the new one.
    vi.mocked(api.patchDraft).mockClear();
    vi.mocked(api.commitDraft).mockClear();
    const order: string[] = [];
    vi.mocked(api.patchDraft).mockImplementation(() => {
      order.push("patch");
      return Promise.resolve();
    });
    vi.mocked(api.commitDraft).mockImplementation(() => {
      order.push("commit");
      return Promise.resolve();
    });

    render(<ExpenseDraftCard message={itemized} members={members} roomId={3} />);
    // Edit a price, then hit Record immediately — inside the debounce window.
    fireEvent.change(screen.getByLabelText("Item price for Emi"), { target: { value: "80000" } });
    fireEvent.click(screen.getByRole("button", { name: /record now/i }));

    await vi.waitFor(() => expect(order).toContain("commit"));
    expect(order).toEqual(["patch", "commit"]);
    // The flushed body carries the edited price, not the one on mount (69,500).
    const flushed = vi.mocked(api.patchDraft).mock.calls[0][2] as { items: { amount: number }[] };
    expect(flushed.items.some((i) => i.amount === 80_000)).toBe(true);
  });

  it("does not commit when the flush is rejected", async () => {
    vi.mocked(api.patchDraft).mockClear();
    vi.mocked(api.commitDraft).mockClear();
    vi.mocked(api.patchDraft).mockImplementation(() =>
      Promise.reject(new api.ApiError(400, "Chia đều phần giảm sẽ làm phần của một người âm")));

    render(<ExpenseDraftCard message={itemized} members={members} roomId={3} />);
    fireEvent.change(screen.getByLabelText("Item price for Emi"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: /record now/i }));

    await vi.waitFor(() => expect(screen.getByText(/âm/)).toBeInTheDocument());
    expect(api.commitDraft).not.toHaveBeenCalled();
  });
});
