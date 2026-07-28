import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PayActions } from "../pay-actions";
import { BotMessage } from "../bot-message";

const QR =
  "https://img.vietqr.io/image/TPB-03924686701-compact2.png" +
  "?amount=107000&addInfo=Linh%3A%20T2&accountName=NGUYEN%20VAN%20A";

const ANDROID_UA =
  "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Mobile Safari/537.36";
const DESKTOP_UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36";

const setUA = (ua: string) =>
  Object.defineProperty(window.navigator, "userAgent", { value: ua, configurable: true });

const mockSession = vi.hoisted(() => ({ memberId: 6 as number | null }));
vi.mock("@/lib/session", () => ({ useSession: () => mockSession }));

beforeEach(() => {
  localStorage.clear();
  mockSession.memberId = 6;
  setUA(ANDROID_UA);
});

describe("PayActions — launching the bank app", () => {
  it("defaults to the app for the member's own bank, not the payee's", () => {
    // Profile cache says this member banks with Vietcombank; the payee is at
    // TPBank. The button must open VCB and pay *to* tpb.
    localStorage.setItem("chiatienan.profile", JSON.stringify({ bank_code: "VCB" }));
    render(<PayActions qrUrl={QR} amount={107_000} note="Linh: T2" />);

    const link = screen.getByRole("link", { name: /Mở Vietcombank/i });
    const href = link.getAttribute("href")!;
    expect(href).toContain("app=vcb");
    expect(href).toContain("ba=03924686701@tpb");
    expect(href).toContain("am=107000");
    expect(href).toContain("tn=Linh%3A%20T2");
  });

  it("prefers the app the member last paid from over their profile bank", () => {
    localStorage.setItem("chiatienan.profile", JSON.stringify({ bank_code: "VCB" }));
    localStorage.setItem("chiatienan.bankApp", "acb");
    render(<PayActions qrUrl={QR} amount={1000} note="x" />);
    expect(screen.getByRole("link", { name: /Mở ACB One/i })).toBeInTheDocument();
  });

  it("offers a picker when we cannot guess the member's app", () => {
    // No profile bank, nothing remembered.
    render(<PayActions qrUrl={QR} amount={1000} note="x" />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Mở app ngân hàng" }));
    expect(screen.getByRole("dialog", { name: /Chọn app/ })).toBeInTheDocument();
  });

  it("remembers the app picked from the sheet", async () => {
    // jsdom has no navigation; stub the assignment the picker makes.
    const loc = { href: "" };
    Object.defineProperty(window, "location", { value: loc, configurable: true });

    render(<PayActions qrUrl={QR} amount={1000} note="x" />);
    fireEvent.click(screen.getByRole("button", { name: "Mở app ngân hàng" }));
    fireEvent.click(screen.getByRole("button", { name: /MB Bank/ }));

    await waitFor(() => expect(localStorage.getItem("chiatienan.bankApp")).toBe("mb"));
    expect(loc.href).toContain("app=mb");
  });

  it("shows no launch button on desktop, where there is no app to open", () => {
    setUA(DESKTOP_UA);
    localStorage.setItem("chiatienan.profile", JSON.stringify({ bank_code: "VCB" }));
    render(<PayActions qrUrl={QR} amount={1000} note="x" />);
    expect(screen.queryByRole("link", { name: /Mở/ })).not.toBeInTheDocument();
    // Copy still works there — that is the point of having both.
    expect(screen.getByRole("button", { name: /Sao chép Số TK/ })).toBeInTheDocument();
  });

  it("renders nothing when the QR URL is unparseable", () => {
    const { container } = render(<PayActions qrUrl="https://x.test/nope.png" amount={1} note="" />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("PayActions — copy fallbacks", () => {
  it("copies the account number, which works for any bank", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(<PayActions qrUrl={QR} amount={107_000} note="Linh: T2" />);
    fireEvent.click(screen.getByRole("button", { name: /Sao chép Số TK/ }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("03924686701"));
    expect(await screen.findByText("✓ Đã chép")).toBeInTheDocument();
  });

  it("copies a bare integer amount, ready to paste into a bank form", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(<PayActions qrUrl={QR} amount={107_000} note="Linh: T2" />);
    fireEvent.click(screen.getByRole("button", { name: /Sao chép Số tiền/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("107000"));
  });

  it("keeps its label when the clipboard is blocked", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    Object.assign(navigator, { clipboard: { writeText } });

    render(<PayActions qrUrl={QR} amount={1000} note="x" />);
    fireEvent.click(screen.getByRole("button", { name: /Sao chép Số TK/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(screen.queryByText("✓ Đã chép")).not.toBeInTheDocument();
  });
});

describe("who sees the pay actions", () => {
  const settlement = (settled: boolean) => ({
    type: "settlement",
    period: { from: null, to: "2026-07-27" },
    transfers: [{
      from_id: 6, to_id: 9, from_name: "Linh Nguyen", to_name: "Giang Hoàng",
      amount: 107_000, note: "Linh: T2", qr_url: QR, settled,
    }],
  });

  beforeEach(() => {
    localStorage.setItem("chiatienan.profile", JSON.stringify({ bank_code: "VCB" }));
  });

  it("shows them to the debtor", () => {
    mockSession.memberId = 6;
    render(<BotMessage body="Tạm tính:" attachments={settlement(false)} roomId={3} />);
    expect(screen.getByRole("link", { name: /Mở Vietcombank/i })).toBeInTheDocument();
  });

  it("hides them from the creditor, who is owed rather than paying", () => {
    mockSession.memberId = 9;
    render(<BotMessage body="Tạm tính:" attachments={settlement(false)} roomId={3} />);
    expect(screen.queryByRole("link", { name: /Mở/ })).not.toBeInTheDocument();
    // The QR stays visible to everyone — handing a phone across the table works.
    expect(screen.getByAltText(/QR to transfer/)).toBeInTheDocument();
  });

  it("hides them from a bystander in the room", () => {
    mockSession.memberId = 42;
    render(<BotMessage body="Tạm tính:" attachments={settlement(false)} roomId={3} />);
    expect(screen.queryByRole("link", { name: /Mở/ })).not.toBeInTheDocument();
  });

  it("hides them once the debt is settled, alongside the QR", () => {
    mockSession.memberId = 6;
    render(<BotMessage body="Tạm tính:" attachments={settlement(true)} roomId={3} />);
    expect(screen.queryByRole("link", { name: /Mở/ })).not.toBeInTheDocument();
    expect(screen.getByText(/không cần trả nữa/)).toBeInTheDocument();
  });
});
