import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Composer } from "../composer";

// Composer fetches the bot handle on mount; stub it so tests don't hit the network.
vi.mock("@/lib/api", () => ({ botHandle: () => Promise.resolve("bot") }));

afterEach(() => vi.unstubAllGlobals());

/** Stub window.matchMedia so the coarse-pointer branch is exercisable in jsdom. */
const stubPointer = (coarse: boolean) =>
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: coarse && q.includes("coarse"),
    media: q,
    addEventListener() {},
    removeEventListener() {},
  }));

describe("Composer Enter-to-send", () => {
  it("Enter sends the message and clears the input", async () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    render(<Composer onSend={onSend} />);
    const textarea = screen.getByLabelText("Compose message") as HTMLTextAreaElement;

    fireEvent.change(textarea, { target: { value: "hello" } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(onSend).toHaveBeenCalledWith("hello", undefined);
    await waitFor(() => expect(textarea.value).toBe(""));
  });

  it("Shift+Enter does not send (multi-line editing preserved)", () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    render(<Composer onSend={onSend} />);
    const textarea = screen.getByLabelText("Compose message");

    fireEvent.change(textarea, { target: { value: "line one" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });

    expect(onSend).not.toHaveBeenCalled();
  });

  it("Enter sends on touch devices too (coarse pointer)", () => {
    stubPointer(true);
    const onSend = vi.fn().mockResolvedValue(undefined);
    render(<Composer onSend={onSend} />);
    const textarea = screen.getByLabelText("Compose message");

    fireEvent.change(textarea, { target: { value: "on mobile" } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(onSend).toHaveBeenCalledWith("on mobile", undefined);
  });
});
