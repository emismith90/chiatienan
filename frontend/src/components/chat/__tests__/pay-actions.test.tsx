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

/** jsdom cannot navigate; capture what the launch assigns to location.href. */
let loc: { href: string };

beforeEach(() => {
  localStorage.clear();
  mockSession.memberId = 6;
  setUA(ANDROID_UA);
  loc = { href: "" };
  Object.defineProperty(window, "location", { value: loc, configurable: true });
});

describe("PayActions — launching the bank app", () => {
  it("opens the app for the member's REGISTERED bank, not the payee's", () => {
    // The roster says this member banks with Vietcombank; the payee is at
    // TPBank. It is the payer's app that has to open.
    render(
      <PayActions qrUrl={QR} amount={107_000} note="Linh: T2" payerBankCode="VCB" />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Mở Vietcombank/i }));
    expect(loc.href).toContain("scheme=vietcombankmobile");
    expect(loc.href).toContain("package=com.VCB");
  });

  it("prefers the registered bank over the device's stale profile cache", () => {
    // The cache is per-device and empty for anyone who signed in elsewhere, so
    // the roster value has to win where the two disagree.
    localStorage.setItem("chiatienan.profile", JSON.stringify({ bank_code: "ACB" }));
    render(<PayActions qrUrl={QR} amount={1000} note="x" payerBankCode="VCB" />);
    expect(screen.getByRole("button", { name: /Mở Vietcombank/i })).toBeInTheDocument();
  });

  it("still falls back to the profile cache when the roster has no bank", () => {
    localStorage.setItem("chiatienan.profile", JSON.stringify({ bank_code: "VCB" }));
    render(<PayActions qrUrl={QR} amount={1000} note="x" payerBankCode={null} />);
    expect(screen.getByRole("button", { name: /Mở Vietcombank/i })).toBeInTheDocument();
  });

  it("launches a scheme a phone actually registers, and no third party", () => {
    // Both failures this has had, pinned: routing via dl.vietqr.io put a page in
    // the way, and `vietqr://pay?…` made iOS say "the address is invalid".
    render(<PayActions qrUrl={QR} amount={107_000} note="Linh: T2" payerBankCode="VCB" />);
    fireEvent.click(screen.getByRole("button", { name: /Mở Vietcombank/i }));
    expect(loc.href).not.toContain("dl.vietqr.io");
    expect(loc.href).not.toContain("vietqr:");
    expect(loc.href).toContain("scheme=vietcombankmobile");
  });

  it("wraps the scheme in an intent on Android, for Chrome's own fallback", () => {
    render(<PayActions qrUrl={QR} amount={1000} note="x" payerBankCode="TCB" />);
    fireEvent.click(screen.getByRole("button", { name: /Mở Techcombank/i }));
    expect(loc.href.startsWith("intent://")).toBe(true);
    expect(loc.href).toContain("scheme=tcb");
    expect(loc.href).toContain("browser_fallback_url");
  });

  it("prefers the app the member last paid from over their registered bank", () => {
    localStorage.setItem("chiatienan.bankApp", "acb");
    render(<PayActions qrUrl={QR} amount={1000} note="x" payerBankCode="VCB" />);
    expect(screen.getByRole("button", { name: /Mở ACB One/i })).toBeInTheDocument();
  });

  it("offers a picker when we cannot guess the member's app", () => {
    // No profile bank, nothing remembered.
    render(<PayActions qrUrl={QR} amount={1000} note="x" />);
    fireEvent.click(screen.getByRole("button", { name: "Mở app ngân hàng" }));
    expect(screen.getByRole("dialog", { name: /Chọn app/ })).toBeInTheDocument();
  });

  it("remembers the app picked from the sheet, and launches it", async () => {
    render(<PayActions qrUrl={QR} amount={1000} note="x" />);
    fireEvent.click(screen.getByRole("button", { name: "Mở app ngân hàng" }));
    fireEvent.click(screen.getByRole("button", { name: /MB Bank/ }));

    await waitFor(() => expect(localStorage.getItem("chiatienan.bankApp")).toBe("mb"));
    expect(loc.href).toContain("scheme=mbbank");
  });

  it("shows no launch button on desktop, where there is no app to open", () => {
    setUA(DESKTOP_UA);
    localStorage.setItem("chiatienan.profile", JSON.stringify({ bank_code: "VCB" }));
    render(<PayActions qrUrl={QR} amount={1000} note="x" payerBankCode="VCB" />);
    expect(screen.queryByRole("button", { name: /^Mở/ })).not.toBeInTheDocument();
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

  // Member 6 banks with Vietcombank, per the roster the room already loaded.
  const roster = [{ id: 6, bank_code: "VCB" }, { id: 9, bank_code: "TPB" }];

  it("shows them to the debtor, opening the debtor's own bank", () => {
    mockSession.memberId = 6;
    render(<BotMessage body="Tạm tính:" attachments={settlement(false)} roomId={3}
                       members={roster} />);
    expect(screen.getByRole("button", { name: /Mở Vietcombank/i })).toBeInTheDocument();
  });

  it("hides them from the creditor, who is owed rather than paying", () => {
    mockSession.memberId = 9;
    render(<BotMessage body="Tạm tính:" attachments={settlement(false)} roomId={3}
                       members={roster} />);
    expect(screen.queryByRole("button", { name: /^Mở/ })).not.toBeInTheDocument();
    // The QR stays visible to everyone — handing a phone across the table works.
    expect(screen.getByAltText(/QR to transfer/)).toBeInTheDocument();
  });

  it("hides them from a bystander in the room", () => {
    mockSession.memberId = 42;
    render(<BotMessage body="Tạm tính:" attachments={settlement(false)} roomId={3}
                       members={roster} />);
    expect(screen.queryByRole("button", { name: /^Mở/ })).not.toBeInTheDocument();
  });

  it("hides them once the debt is settled, alongside the QR", () => {
    mockSession.memberId = 6;
    render(<BotMessage body="Tạm tính:" attachments={settlement(true)} roomId={3}
                       members={roster} />);
    expect(screen.queryByRole("button", { name: /^Mở/ })).not.toBeInTheDocument();
    expect(screen.getByText(/không cần trả nữa/)).toBeInTheDocument();
  });
});
