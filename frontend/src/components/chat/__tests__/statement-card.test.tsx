import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import * as api from "@/lib/api";
import { BotMessage } from "../bot-message";

const att = {
  type: "statement", member: { id: 9, name: "Giang" },
  period: { from: null, to: "2026-07-22" },
  owe: [{ creditor_id: 6, name: "Linh", meal_id: 2, dish: "bun bo", occurred_on: "2026-07-21", amount: 61000, status: "unpaid" }],
  owed: [],
};

beforeEach(() => vi.restoreAllMocks());

describe("StatementCard via BotMessage", () => {
  it("shows what you owe and the meal, and no net total", () => {
    render(<BotMessage body="" attachments={att} roomId={3} />);
    expect(screen.getByText("Linh")).toBeInTheDocument();
    expect(screen.getByText(/bun bo/)).toBeInTheDocument();
    expect(screen.getByText(/61\.000/)).toBeInTheDocument();   // the debt itself
    expect(screen.queryByText("Net")).not.toBeInTheDocument();
    expect(screen.queryByText(/-61\.000/)).not.toBeInTheDocument();
  });

  it("says so plainly when there is nothing either way", () => {
    render(<BotMessage body="" attachments={{ ...att, owe: [], owed: [] }} roomId={3} />);
    expect(screen.getByText(/You owe nobody/)).toBeInTheDocument();
  });

  it("Mark paid records the meal and flips the row", async () => {
    const spy = vi.spyOn(api, "quickPay").mockResolvedValue({ ok: true, payment_id: 1, amount: 61000 });
    render(<BotMessage body="" attachments={att} roomId={3} />);
    fireEvent.click(screen.getByRole("button", { name: /Mark paid/ }));
    expect(spy).toHaveBeenCalledWith(3, 6, 2);
    await waitFor(() => expect(screen.getByText(/· paid/)).toBeInTheDocument());
  });

  it("shows an error hint and leaves the row unpaid when Mark paid fails", async () => {
    vi.spyOn(api, "quickPay").mockRejectedValue(new Error("network"));
    render(<BotMessage body="" attachments={att} roomId={3} />);
    fireEvent.click(screen.getByRole("button", { name: /Mark paid/ }));
    await waitFor(() => expect(screen.getByText(/Failed/)).toBeInTheDocument());
    // Row stays unpaid: button still present, no "· paid" flip.
    expect(screen.getByRole("button", { name: /Mark paid/ })).toBeInTheDocument();
    expect(screen.queryByText(/· paid/)).not.toBeInTheDocument();
  });
});

const qrPayload: api.QrRequest = {
  ok: true,
  amount: 61000,
  note: "Giang: T3 bun bo",
  qr_url: "https://img.vietqr.io/image/VCB-0123456789-compact.png?amount=61000",
  from: { id: 9, name: "Giang", bank_code: "TCB" },
  to: { id: 6, name: "Linh", account_number: "0123456789", account_holder: "LINH NGUYEN", bank_code: "VCB" },
  period: { from: null, to: "2026-07-22" },
  meals: [{ meal_id: 2, dish: "bun bo", date: "2026-07-21", amount: 61000 }],
};

describe("OweRow QR button", () => {
  it("opens the QR in a dialog — not in the chat — for that creditor", async () => {
    const spy = vi.spyOn(api, "requestQr").mockResolvedValue(qrPayload);
    render(<BotMessage body="" attachments={att} roomId={3} />);
    fireEvent.click(screen.getByRole("button", { name: "QR" }));
    expect(spy).toHaveBeenCalledWith(3, 6);

    const dialog = await screen.findByRole("dialog", { name: "Pay Linh" });
    expect(within(dialog).getByRole("img", { name: /transfer .* to Linh/ }))
      .toHaveAttribute("src", qrPayload.qr_url);
    // Headline total, and again on the one meal it is made of.
    expect(within(dialog).getAllByText("61.000 đ")).toHaveLength(2);
    // The payee's details and what the total is made of, both server-computed.
    expect(within(dialog).getByText("LINH NGUYEN")).toBeInTheDocument();
    expect(within(dialog).getByText(/Giang: T3 bun bo/)).toBeInTheDocument();
    expect(within(dialog).getByText(/21\/7 · bun bo/)).toBeInTheDocument();
  });

  it("closes the dialog again", async () => {
    vi.spyOn(api, "requestQr").mockResolvedValue(qrPayload);
    render(<BotMessage body="" attachments={att} roomId={3} />);
    fireEvent.click(screen.getByRole("button", { name: "QR" }));
    const dialog = await screen.findByRole("dialog", { name: "Pay Linh" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Pay Linh" })).not.toBeInTheDocument(),
    );
  });

  it("surfaces the server's reason when the QR cannot be built", async () => {
    vi.spyOn(api, "requestQr").mockRejectedValue(
      new api.ApiError(409, "Linh has no bank details yet — please update them on /profile."),
    );
    render(<BotMessage body="" attachments={att} roomId={3} />);
    fireEvent.click(screen.getByRole("button", { name: "QR" }));
    await waitFor(() => expect(screen.getByText(/no bank details/)).toBeInTheDocument());
  });

  it("offers no QR on a paid row", () => {
    const paid = { ...att, owe: [{ ...att.owe[0], status: "paid" }] };
    render(<BotMessage body="" attachments={paid} roomId={3} />);
    expect(screen.queryByRole("button", { name: "QR" })).not.toBeInTheDocument();
  });
});
