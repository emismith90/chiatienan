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
    expect(screen.getByText(/No transactions/)).toBeInTheDocument();
  });
});

describe("grouping by day", () => {
  it("puts each day under its own heading, newest first", async () => {
    const { groupByDay } = await import("../transaction-timeline");
    const days = groupByDay(events);
    expect(days.map((d) => d.day)).toEqual(["2026-07-22", "2026-07-21"]);
    expect(days[0].moved).toBe(61_000);
    expect(days[0].spent).toBe(0);
    expect(days[1].spent).toBe(305_000);
  });

  it("keeps meal spend and money moved separate", async () => {
    const { groupByDay } = await import("../transaction-timeline");
    const sameDay = [
      { ...events[0], occurred_on: "2026-07-22" },
      events[1],
    ] as any;
    const [day] = groupByDay(sameDay);
    // Not summed: 305,000đ was fronted on food, 61,000đ changed hands.
    expect(day.spent).toBe(305_000);
    expect(day.moved).toBe(61_000);
  });

  it("collapses older days behind a toggle and expands on click", async () => {
    const { fireEvent } = await import("@testing-library/react");
    const many = Array.from({ length: 5 }, (_, i) => ({
      kind: "meal", meal_id: i + 10, payer_id: 6, payer_name: "Linh",
      dish: `day${i}`, occurred_on: `2026-07-2${i}`, total: 1000,
      participant_ids: [6], created_at: "",
    })) as any;

    render(<TransactionTimeline events={many} />);
    // Newest three days open, the two oldest behind the toggle.
    expect(screen.getByText(/day4/)).toBeInTheDocument();
    expect(screen.queryByText(/day0/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /2 earlier days/ }));
    expect(screen.getByText(/day0/)).toBeInTheDocument();
  });
});
