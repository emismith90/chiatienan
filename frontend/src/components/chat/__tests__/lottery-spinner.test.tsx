import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { LotterySpinner, awaitingRandomPick, looksLikeRandomRequest } from "../lottery-spinner";

describe("looksLikeRandomRequest", () => {
  it("matches random-pick phrasing, with or without diacritics", () => {
    for (const s of [
      "bốc thăm ai trả",
      "boc tham ai tra",
      "random một người",
      "chọn đại ai đi mua",
      "xổ số đi",
      "BỐC THĂM",
    ]) {
      expect(looksLikeRandomRequest(s)).toBe(true);
    }
  });

  it("does not fire on ordinary messages", () => {
    for (const s of ["An trả tiền hôm nay", "chia tiền bữa trưa", "hello", "", null, undefined]) {
      expect(looksLikeRandomRequest(s)).toBe(false);
    }
  });
});

describe("awaitingRandomPick", () => {
  it("spins for a person's unanswered random-pick request", () => {
    expect(awaitingRandomPick({ author: { id: 3 }, body: "bốc thăm ai trả" })).toBe(true);
  });

  it("never spins under a bot message, whatever its text", () => {
    // prod 2026-10-07: a record_draft card listing the alias "bock bock" ("boc")
    expect(awaitingRandomPick({ author: null, body: "aliases: koko, gà rán, bock bock → gà koko" })).toBe(false);
  });

  it("does not spin for a failed or queued message, or no message", () => {
    expect(awaitingRandomPick({ author: { id: 3 }, body: "random", error: "x" })).toBe(false);
    expect(awaitingRandomPick({ author: { id: 3 }, body: "random", queued: true })).toBe(false);
    expect(awaitingRandomPick(null)).toBe(false);
  });
});

describe("LotterySpinner", () => {
  afterEach(() => vi.useRealTimers());

  it("announces the draw and cycles through the roster names", () => {
    vi.useFakeTimers();
    render(<LotterySpinner names={["An", "Bình", "Chi"]} />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-label", "Drawing at random…");
    // A name from the roster is shown, and it advances on a timer.
    expect(screen.getByText("An")).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(90);
    });
    expect(screen.getByText("Bình")).toBeInTheDocument();
  });

  it("renders a placeholder when the roster is empty", () => {
    render(<LotterySpinner names={[]} />);
    expect(screen.getByText("…")).toBeInTheDocument();
  });
});
