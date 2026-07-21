import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { SUGGESTIONS, SuggestionChips } from "../suggestion-chips";

describe("SuggestionChips", () => {
  it("renders a button for every suggestion", () => {
    render(<SuggestionChips onPick={() => {}} />);
    for (const s of SUGGESTIONS) {
      expect(screen.getByRole("button", { name: new RegExp(s.label) })).toBeInTheDocument();
    }
  });

  it("calls onPick with the suggestion's prefill text when a chip is tapped", () => {
    const onPick = vi.fn();
    render(<SuggestionChips onPick={onPick} />);

    fireEvent.click(screen.getByRole("button", { name: new RegExp(SUGGESTIONS[0].label) }));

    expect(onPick).toHaveBeenCalledWith(SUGGESTIONS[0].text);
  });

  it("suggests capabilities that address @bot in English", () => {
    for (const s of SUGGESTIONS) {
      expect(s.text.startsWith("@bot ")).toBe(true);
    }
  });
});
