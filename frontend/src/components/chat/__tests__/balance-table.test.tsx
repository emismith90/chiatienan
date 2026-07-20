import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { BalanceTable } from "../balance-table";

const rows = [
  { id: 1, name: "An", paid: 200_000, consumed: 100_000, balance: 100_000 },
  { id: 2, name: "Binh", paid: 0, consumed: 100_000, balance: -100_000 },
];

describe("BalanceTable", () => {
  it("renders nothing when rows is empty", () => {
    const { container } = render(<BalanceTable rows={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when rows is missing", () => {
    // @ts-expect-error - exercising the runtime guard for undefined rows
    const { container } = render(<BalanceTable rows={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders each person's name, paid, consumed, and signed balance", () => {
    render(<BalanceTable rows={rows} />);
    expect(screen.getByText("An")).toBeInTheDocument();
    expect(screen.getByText("Binh")).toBeInTheDocument();
    expect(screen.getByText("+100.000")).toBeInTheDocument();
    expect(screen.getByText("-100.000")).toBeInTheDocument();
  });

  it("shows the section heading", () => {
    render(<BalanceTable rows={rows} />);
    expect(screen.getByText("Số dư hiện tại")).toBeInTheDocument();
  });
});
