import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { BalanceBars } from "../balance-bars";

const rows = [
  { id: 9, name: "Giang", balance: 89000 },
  { id: 5, name: "Trang", balance: -75000 },
];

describe("BalanceBars", () => {
  it("renders nothing when empty", () => {
    const { container } = render(<BalanceBars rows={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
  it("renders signed balances and marks self", () => {
    render(<BalanceBars rows={rows} selfId={9} />);
    expect(screen.getByText("+89.000")).toBeInTheDocument();
    expect(screen.getByText("-75.000")).toBeInTheDocument();
    expect(screen.getByText("Giang")).toHaveAttribute("data-self", "true");
  });
});
