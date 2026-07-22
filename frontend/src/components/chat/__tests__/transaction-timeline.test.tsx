import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { TransactionTimeline } from "../transaction-timeline";

const events = [
  { kind: "meal", meal_id: 2, payer_id: 6, payer_name: "Linh", dish: "bun bo",
    occurred_on: "2026-07-21", total: 305000, participant_ids: [6, 9], created_at: "" },
  { kind: "payment", payment_id: 1, from_id: 9, to_id: 6, from_name: "Giang",
    to_name: "Linh", amount: 61000, occurred_on: "2026-07-22", created_at: "" },
] as any;

describe("TransactionTimeline", () => {
  it("renders meals and payments", () => {
    render(<TransactionTimeline events={events} />);
    expect(screen.getByText(/bun bo/)).toBeInTheDocument();
    expect(screen.getByText(/Giang/)).toBeInTheDocument();
    // "Linh" appears as both meal payer and payment recipient
    expect(screen.getAllByText(/Linh/).length).toBeGreaterThanOrEqual(1);
  });
  it("renders an empty note when there are no events", () => {
    render(<TransactionTimeline events={[]} />);
    expect(screen.getByText(/Chưa có giao dịch/)).toBeInTheDocument();
  });
});
