import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { BotMessage } from "../bot-message";

vi.mock("next/image", () => ({
  default: (props: any) => <img {...props} alt={props.alt} />,
}));

const settlement = (settled: boolean) => ({
  type: "settlement",
  period: { from: null, to: "2026-07-27" },
  transfers: [{
    from_id: 6, to_id: 9, from_name: "Linh Nguyen", to_name: "Giang Hoàng",
    amount: 107_000, note: "x",
    qr_url: "https://img.vietqr.io/image/TPB-1-compact2.png?amount=107000",
    settled,
  }],
});

describe("settled settlement QRs", () => {
  it("shows the QR while the debt is outstanding", () => {
    render(<BotMessage body="Tạm tính đến 2026-07-27:" attachments={settlement(false)} roomId={3} />);
    expect(screen.getByAltText(/QR to transfer/)).toBeInTheDocument();
  });

  it("hides the QR once the debt is settled", () => {
    // Production msg#153/#161: a transfer that was never owed, still carrying a
    // scannable QR in the thread. Tapping it pays money nobody owes.
    render(<BotMessage body="Tạm tính đến 2026-07-27:" attachments={settlement(true)} roomId={3} />);
    expect(screen.queryByAltText(/QR to transfer/)).not.toBeInTheDocument();
    expect(screen.getByText(/không cần trả nữa/)).toBeInTheDocument();
  });

  it("still shows who owed whom, so history stays readable", () => {
    render(<BotMessage body="Tạm tính đến 2026-07-27:" attachments={settlement(true)} roomId={3} />);
    expect(screen.getByText(/Linh Nguyen/)).toBeInTheDocument();
    expect(screen.getByText(/107\.000/)).toBeInTheDocument();
  });
});
