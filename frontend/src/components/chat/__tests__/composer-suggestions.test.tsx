import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Composer } from "../composer";
import { SUGGESTIONS } from "../suggestion-chips";

// Composer fetches the bot handle on mount; stub it so tests don't hit the network.
vi.mock("@/lib/api", () => ({ botHandle: () => Promise.resolve("bot") }));

describe("Composer suggestion chips", () => {
  it("shows capability chips while the composer is empty", () => {
    render(<Composer onSend={() => {}} />);
    expect(screen.getByRole("button", { name: new RegExp(SUGGESTIONS[0].label) })).toBeInTheDocument();
  });

  it("prefills the textarea with the chip's message when tapped", () => {
    render(<Composer onSend={() => {}} />);

    fireEvent.click(screen.getByRole("button", { name: new RegExp(SUGGESTIONS[1].label) }));

    const textarea = screen.getByLabelText("Compose message") as HTMLTextAreaElement;
    expect(textarea.value).toBe(SUGGESTIONS[1].text);
  });

  it("hides the chips once the composer has text", () => {
    render(<Composer onSend={() => {}} />);
    const textarea = screen.getByLabelText("Compose message");

    fireEvent.change(textarea, { target: { value: "@bot hi" } });

    expect(screen.queryByRole("button", { name: new RegExp(SUGGESTIONS[0].label) })).not.toBeInTheDocument();
  });
});
