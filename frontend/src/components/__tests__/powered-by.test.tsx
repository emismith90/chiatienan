import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { PoweredBy, ENGINE_NAME } from "../powered-by";

describe("PoweredBy", () => {
  it("names the engine in both variants", () => {
    const { rerender } = render(<PoweredBy />);
    expect(screen.getByText(new RegExp(ENGINE_NAME))).toBeInTheDocument();
    rerender(<PoweredBy variant="splash" />);
    expect(screen.getByText(new RegExp(ENGINE_NAME))).toBeInTheDocument();
  });

  it("keeps the logo lockup out of the accessibility tree", () => {
    // "chiatienan × DeepSeek" is a picture of a sentence the text already says.
    // Exposing it would make a screen reader announce the app icon, a stray
    // multiplication sign and the whale before reaching the words.
    const { container } = render(<PoweredBy variant="splash" />);
    expect(screen.queryByRole("img", { name: "DeepSeek" })).toBeNull();
    expect(container.querySelector("[aria-hidden]")).toContainElement(
      container.querySelector("svg"),
    );
  });

  it("cache-busts the app icon the same way layout.tsx does", () => {
    // Same file, same URL: a bare /icon-192.png here would be a second cache
    // entry for identical bytes, and would miss the ?v= bump when the art moves.
    const { container } = render(<PoweredBy variant="splash" />);
    expect(container.querySelector("img")?.getAttribute("src")).toBe("/icon-192.png?v=2");
  });
});
