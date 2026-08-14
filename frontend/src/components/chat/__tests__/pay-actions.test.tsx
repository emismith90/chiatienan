import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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

afterEach(() => {
  // The save tests stub fetch/URL/navigator.share; left in place they break
  // every test after them in the file.
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  Object.assign(navigator, { share: undefined, canShare: undefined });
  delete (URL as unknown as Record<string, unknown>).createObjectURL;
  delete (URL as unknown as Record<string, unknown>).revokeObjectURL;
});

describe("PayActions — launching the bank app", () => {
  it("opens the app for the member's REGISTERED bank, not the payee's", () => {
    // The roster says this member banks with Vietcombank; the payee is at
    // TPBank. It is the payer's app that has to open.
    render(
      <PayActions qrUrl={QR} amount={107_000} note="Linh: T2" payerBankCode="VCB" />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Open Vietcombank/i }));
    expect(loc.href).toContain("scheme=vietcombankmobile");
    expect(loc.href).toContain("package=com.VCB");
  });

  it("prefers the registered bank over the device's stale profile cache", () => {
    // The cache is per-device and empty for anyone who signed in elsewhere, so
    // the roster value has to win where the two disagree.
    localStorage.setItem("chiatienan.profile", JSON.stringify({ bank_code: "ACB" }));
    render(<PayActions qrUrl={QR} amount={1000} note="x" payerBankCode="VCB" />);
    expect(screen.getByRole("button", { name: /Open Vietcombank/i })).toBeInTheDocument();
  });

  it("still falls back to the profile cache when the roster has no bank", () => {
    localStorage.setItem("chiatienan.profile", JSON.stringify({ bank_code: "VCB" }));
    render(<PayActions qrUrl={QR} amount={1000} note="x" payerBankCode={null} />);
    expect(screen.getByRole("button", { name: /Open Vietcombank/i })).toBeInTheDocument();
  });

  it("launches a scheme a phone actually registers, and no third party", () => {
    // Both failures this has had, pinned: routing via dl.vietqr.io put a page in
    // the way, and `vietqr://pay?…` made iOS say "the address is invalid".
    render(<PayActions qrUrl={QR} amount={107_000} note="Linh: T2" payerBankCode="VCB" />);
    fireEvent.click(screen.getByRole("button", { name: /Open Vietcombank/i }));
    expect(loc.href).not.toContain("dl.vietqr.io");
    expect(loc.href).not.toContain("vietqr:");
    expect(loc.href).toContain("scheme=vietcombankmobile");
  });

  it("wraps the scheme in an intent on Android, for Chrome's own fallback", () => {
    render(<PayActions qrUrl={QR} amount={1000} note="x" payerBankCode="TCB" />);
    fireEvent.click(screen.getByRole("button", { name: /Open Techcombank/i }));
    expect(loc.href.startsWith("intent://")).toBe(true);
    expect(loc.href).toContain("scheme=tcb");
    expect(loc.href).toContain("browser_fallback_url");
  });

  it("prefers the app the member last paid from over their registered bank", () => {
    localStorage.setItem("chiatienan.bankApp", "acb");
    render(<PayActions qrUrl={QR} amount={1000} note="x" payerBankCode="VCB" />);
    expect(screen.getByRole("button", { name: /Open ACB One/i })).toBeInTheDocument();
  });

  it("offers a picker when we cannot guess the member's app", () => {
    // No profile bank, nothing remembered.
    render(<PayActions qrUrl={QR} amount={1000} note="x" />);
    fireEvent.click(screen.getByRole("button", { name: "Open banking app" }));
    expect(screen.getByRole("dialog", { name: /Pick a banking app/ })).toBeInTheDocument();
  });

  it("remembers the app picked from the sheet, and launches it", async () => {
    render(<PayActions qrUrl={QR} amount={1000} note="x" />);
    fireEvent.click(screen.getByRole("button", { name: "Open banking app" }));
    fireEvent.click(screen.getByRole("button", { name: /MB Bank/ }));

    await waitFor(() => expect(localStorage.getItem("chiatienan.bankApp")).toBe("mb"));
    expect(loc.href).toContain("scheme=mbbank");
  });

  it("shows no launch button on desktop, where there is no app to open", () => {
    setUA(DESKTOP_UA);
    localStorage.setItem("chiatienan.profile", JSON.stringify({ bank_code: "VCB" }));
    render(<PayActions qrUrl={QR} amount={1000} note="x" payerBankCode="VCB" />);
    expect(screen.queryByRole("button", { name: /^Open/ })).not.toBeInTheDocument();
    // Copy still works there — that is the point of having both.
    expect(screen.getByRole("button", { name: /Copy Account/ })).toBeInTheDocument();
  });

  it("renders nothing when the QR URL is unparseable", () => {
    const { container } = render(<PayActions qrUrl="https://x.test/nope.png" amount={1} note="" />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("PayActions — saving the QR, the only route that pre-fills", () => {
  const pngBlob = () => new Blob([new Uint8Array([137, 80, 78, 71])], { type: "image/png" });

  it("shares the QR file so the camera roll can take it", async () => {
    const share = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { share, canShare: () => true });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: async () => pngBlob() }));

    render(<PayActions qrUrl={QR} amount={107_000} note="Linh: T2" payerBankCode="VCB" />);
    fireEvent.click(screen.getByRole("button", { name: /Save QR/ }));

    await waitFor(() => expect(share).toHaveBeenCalled());
    const [[arg]] = share.mock.calls;
    expect(arg.files).toHaveLength(1);
    expect(arg.files[0].type).toBe("image/png");
    // The amount names the file, so a camera roll full of these is navigable.
    expect(arg.files[0].name).toContain("107000");
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
  });

  it("treats a dismissed share sheet as nothing happening, not a failure", async () => {
    const abort = Object.assign(new Error("cancelled"), { name: "AbortError" });
    Object.assign(navigator, { share: vi.fn().mockRejectedValue(abort), canShare: () => true });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: async () => pngBlob() }));

    render(<PayActions qrUrl={QR} amount={1000} note="x" payerBankCode="VCB" />);
    fireEvent.click(screen.getByRole("button", { name: /Save QR/ }));

    await waitFor(() => expect(screen.getByRole("button", { name: /Save QR/ })).toBeInTheDocument());
    expect(screen.queryByText(/failed/)).not.toBeInTheDocument();
  });

  it("falls back to a download where the share sheet takes no files", async () => {
    Object.assign(navigator, { share: undefined, canShare: () => false });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: async () => pngBlob() }));
    // Only the statics — replacing the whole URL global would break the
    // `new URL(qrUrl)` that parseQrUrl needs, and the component would render
    // nothing at all.
    Object.assign(URL, { createObjectURL: () => "blob:x", revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    render(<PayActions qrUrl={QR} amount={1000} note="x" payerBankCode="VCB" />);
    fireEvent.click(screen.getByRole("button", { name: /Save QR/ }));

    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
  });

  it("says so when the QR cannot be fetched, rather than claiming a save", async () => {
    Object.assign(navigator, { canShare: () => true });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 403 }));

    render(<PayActions qrUrl={QR} amount={1000} note="x" payerBankCode="VCB" />);
    fireEvent.click(screen.getByRole("button", { name: /Save QR/ }));
    expect(await screen.findByText(/failed/)).toBeInTheDocument();
  });

  it("is not offered on desktop, where the QR can just be scanned", () => {
    setUA(DESKTOP_UA);
    render(<PayActions qrUrl={QR} amount={1000} note="x" payerBankCode="VCB" />);
    expect(screen.queryByRole("button", { name: /Save QR/ })).not.toBeInTheDocument();
  });
});

describe("PayActions — copy fallbacks", () => {
  it("copies the account number, which works for any bank", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(<PayActions qrUrl={QR} amount={107_000} note="Linh: T2" />);
    fireEvent.click(screen.getByRole("button", { name: /Copy Account/ }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("03924686701"));
    expect(await screen.findByText("✓ Copied")).toBeInTheDocument();
  });

  it("copies a bare integer amount, ready to paste into a bank form", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(<PayActions qrUrl={QR} amount={107_000} note="Linh: T2" />);
    fireEvent.click(screen.getByRole("button", { name: /Copy Amount/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("107000"));
  });

  it("keeps its label when the clipboard is blocked", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    Object.assign(navigator, { clipboard: { writeText } });

    render(<PayActions qrUrl={QR} amount={1000} note="x" />);
    fireEvent.click(screen.getByRole("button", { name: /Copy Account/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(screen.queryByText("✓ Copied")).not.toBeInTheDocument();
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
    expect(screen.getByRole("button", { name: /Open Vietcombank/i })).toBeInTheDocument();
  });

  it("hides them from the creditor, who is owed rather than paying", () => {
    mockSession.memberId = 9;
    render(<BotMessage body="Tạm tính:" attachments={settlement(false)} roomId={3}
                       members={roster} />);
    expect(screen.queryByRole("button", { name: /^Open/ })).not.toBeInTheDocument();
    // The QR stays visible to everyone — handing a phone across the table works.
    expect(screen.getByAltText(/QR to transfer/)).toBeInTheDocument();
  });

  it("hides them from a bystander in the room", () => {
    mockSession.memberId = 42;
    render(<BotMessage body="Tạm tính:" attachments={settlement(false)} roomId={3}
                       members={roster} />);
    expect(screen.queryByRole("button", { name: /^Open/ })).not.toBeInTheDocument();
  });

  it("hides them once the debt is settled, alongside the QR", () => {
    mockSession.memberId = 6;
    render(<BotMessage body="Tạm tính:" attachments={settlement(true)} roomId={3}
                       members={roster} />);
    expect(screen.queryByRole("button", { name: /^Open/ })).not.toBeInTheDocument();
    expect(screen.getByText(/nothing left to pay/)).toBeInTheDocument();
  });
});
