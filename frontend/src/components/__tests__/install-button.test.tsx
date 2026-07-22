import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { InstallButton } from "../install-button";

function setUserAgent(ua: string, maxTouchPoints = 0) {
  Object.defineProperty(navigator, "userAgent", { value: ua, configurable: true });
  Object.defineProperty(navigator, "maxTouchPoints", { value: maxTouchPoints, configurable: true });
}

const DESKTOP_UA =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36";
const IPHONE_UA =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1";

afterEach(() => {
  setUserAgent(DESKTOP_UA);
  vi.restoreAllMocks();
});

describe("InstallButton", () => {
  it("renders nothing on a desktop browser that never fires beforeinstallprompt", () => {
    setUserAgent(DESKTOP_UA);
    const { container } = render(<InstallButton />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows an install button on iOS and opens the Add to Home Screen sheet", () => {
    setUserAgent(IPHONE_UA);
    render(<InstallButton />);
    const btn = screen.getByRole("button", { name: /install app/i });
    fireEvent.click(btn);
    expect(screen.getByRole("dialog", { name: /install app/i })).toBeInTheDocument();
    expect(screen.getByText(/Add to Home Screen/i)).toBeInTheDocument();
  });

  it("fires the native prompt when beforeinstallprompt was captured", async () => {
    setUserAgent(DESKTOP_UA);
    render(<InstallButton />);
    const prompt = vi.fn();
    const evt: any = new Event("beforeinstallprompt");
    evt.prompt = prompt;
    evt.userChoice = Promise.resolve({ outcome: "accepted" });
    act(() => {
      window.dispatchEvent(evt);
    });

    const btn = await screen.findByRole("button", { name: /install app/i });
    fireEvent.click(btn);
    expect(prompt).toHaveBeenCalledOnce();
    // Native path: no instructions dialog.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
