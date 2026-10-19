import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatementSections } from "../statement-card";

vi.mock("@/lib/api", () => ({ quickPay: vi.fn(), quickReceive: vi.fn(), requestQr: vi.fn() }));

const row = (other_id: number, name: string, meal_id: number, dish: string, amount: number) =>
  ({ other_id, name, meal_id, dish, amount, status: "unpaid" });

const names = () => screen.getAllByRole("listitem").map((li) => li.textContent);

describe("StatementSections rows", () => {
  it("re-renders a shared meal's debtors correctly after one row settles", () => {
    // One meal, three debtors: every row shares the meal_id. Keyed by meal alone,
    // the refetch after a mark-paid left stale and duplicated rows until reload.
    const before = [row(1, "Nhím", 7, "lunch", 65000), row(2, "Linh", 9, "phở Vui", 40000),
                    row(1, "Nhím", 9, "phở Vui", 40000), row(3, "GH", 9, "phở Vui", 40000)];
    const { rerender } = render(<StatementSections owe={[]} owed={before} roomId={1} onPaid={() => {}} />);
    expect(names()).toHaveLength(4);

    rerender(<StatementSections owe={[]} owed={before.slice(1)} roomId={1} onPaid={() => {}} />);
    expect(names()).toEqual([
      expect.stringContaining("Linh"), expect.stringContaining("Nhím"), expect.stringContaining("GH"),
    ]);
  });

  it("keeps one owe row per creditor when a meal had several payers", () => {
    const owe = [row(4, "An", 9, "phở Vui", 20000), row(5, "Bình", 9, "phở Vui", 20000)];
    const { rerender } = render(<StatementSections owe={owe} owed={[]} roomId={1} onPaid={() => {}} />);
    rerender(<StatementSections owe={owe.slice(1)} owed={[]} roomId={1} onPaid={() => {}} />);
    expect(names()).toEqual([expect.stringContaining("Bình")]);
  });
});
