import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { LuckyDrawDialog, SlotReel } from "../lucky-draw";
import { BotMessage } from "../bot-message";
import { mergeEvent } from "@/hooks/use-room";
import * as api from "@/lib/api";

const FAKE = { toFake: ["requestAnimationFrame", "cancelAnimationFrame", "performance", "setTimeout"] } as const;

/** What the reel window shows: the names currently rendered in it. */
const shown = (el: HTMLElement) => Array.from(el.querySelectorAll("[aria-hidden] > div")).map((d) => d.textContent);

describe("SlotReel", () => {
  afterEach(() => vi.useRealTimers());

  it("rolls, then lands exactly on the winner and says so", () => {
    vi.useFakeTimers(FAKE as any);
    const onLanded = vi.fn();
    const names = ["An", "Bình", "Chi", "Dũng"];
    const { container, rerender } = render(
      <SlotReel names={names} winner={null} spinning onLanded={onLanded} />,
    );
    act(() => vi.advanceTimersByTime(500));
    expect(onLanded).not.toHaveBeenCalled();         // no winner yet: keeps rolling

    rerender(<SlotReel names={names} winner="Chi" spinning onLanded={onLanded} />);
    act(() => vi.advanceTimersByTime(5000));
    expect(onLanded).toHaveBeenCalledTimes(1);
    expect(shown(container)).toEqual(["Chi"]);

    rerender(<SlotReel names={names} winner="Chi" spinning={false} onLanded={onLanded} />);
    expect(shown(container)).toEqual(["Chi"]);
  });

  it("lands on a winner that is no longer on the list it was rolling", () => {
    vi.useFakeTimers(FAKE as any);
    const onLanded = vi.fn();
    const { container } = render(
      <SlotReel names={["An", "Bình"]} winner="Chi" spinning onLanded={onLanded} />,
    );
    act(() => vi.advanceTimersByTime(5000));
    expect(onLanded).toHaveBeenCalled();
    expect(shown(container)).toEqual(["Chi"]);
  });

  it("shows the placeholder before any draw", () => {
    const { container } = render(<SlotReel names={["An"]} winner={null} spinning={false} />);
    expect(shown(container)).toEqual(["?"]);
  });
});

describe("LuckyDrawDialog", () => {
  const members = [
    { id: 1, display_name: "An", default_participant: true },
    { id: 2, display_name: "Bình", default_participant: false },
    { id: 3, display_name: "Chi", default_participant: true },
  ];
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.useRealTimers());

  it("starts from the saved list and saves a toggle", async () => {
    const save = vi.spyOn(api, "setDrawPool").mockResolvedValue({ ok: true });
    const changed = vi.fn();
    render(<LuckyDrawDialog roomId={7} members={members} onClose={() => {}} onListChanged={changed} />);
    expect(screen.getByText(/In the draw · 2 of 3/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Bình" })).toHaveAttribute("aria-pressed", "false");

    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Bình" })));
    expect(save).toHaveBeenCalledWith(7, [1, 3, 2]);
    expect(changed).toHaveBeenCalled();
    expect(screen.getByText(/In the draw · 3 of 3/)).toBeInTheDocument();
  });

  it("puts a toggle back when saving fails", async () => {
    vi.spyOn(api, "setDrawPool").mockRejectedValue(new Error("nope"));
    render(<LuckyDrawDialog roomId={7} members={members} onClose={() => {}} />);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "An" })));
    expect(screen.getByRole("button", { name: "An" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("nope");
  });

  it("draws on the server and lands on its winner", async () => {
    vi.useFakeTimers(FAKE as any);
    const draw = vi.spyOn(api, "luckyDraw").mockResolvedValue({
      type: "random_pick", chosen: { id: 3, name: "Chi" },
      candidates: [{ id: 1, name: "An" }, { id: 3, name: "Chi" }],
    });
    render(<LuckyDrawDialog roomId={7} members={members} onClose={() => {}} />);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Draw" })));
    expect(draw).toHaveBeenCalledWith(7);
    expect(screen.getByRole("status")).toHaveTextContent("Drawing…");
    act(() => vi.advanceTimersByTime(5000));
    expect(screen.getByRole("status")).toHaveTextContent("🎉 Chi! Posted to the room.");
    expect(screen.getByRole("button", { name: "Draw again" })).toBeEnabled();
  });

  it("can't draw from an empty list", () => {
    render(<LuckyDrawDialog roomId={7} members={members.map((m) => ({ ...m, default_participant: false }))}
                            onClose={() => {}} />);
    expect(screen.getByRole("button", { name: "Draw" })).toBeDisabled();
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(<LuckyDrawDialog roomId={7} members={members} onClose={onClose} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});

describe("a draw card in the chat", () => {
  afterEach(() => vi.useRealTimers());
  const att = {
    type: "random_pick", chosen: { id: 3, name: "Chi" }, label: null,
    candidates: [{ id: 1, name: "An" }, { id: 3, name: "Chi" }],
    drawn_by: { id: 1, name: "An" },
  };

  it("rolls the reel to the winner when it arrives live", () => {
    vi.useFakeTimers(FAKE as any);
    render(<BotMessage body="🎲" attachments={att} roomId={7} live />);
    expect(screen.queryByText("Picked: Chi")).not.toBeInTheDocument();     // still rolling
    act(() => vi.advanceTimersByTime(5000));
    expect(screen.getByText("Picked: Chi")).toBeInTheDocument();
    expect(screen.getByText(/drawn by An/)).toBeInTheDocument();
  });

  it("opens on the result when it comes from history", () => {
    render(<BotMessage body="🎲" attachments={att} roomId={7} />);
    expect(screen.getByText("Picked: Chi")).toBeInTheDocument();
  });

  it("is marked live only when the stream delivers it", () => {
    const s = mergeEvent({ messages: [], typing: false, timelines: {}, activeTurn: null },
                         { type: "message", id: 9, kind: "bot", body: "🎲", attachments: att });
    expect(s.messages[0].live).toBe(true);
    const other = mergeEvent({ messages: [], typing: false, timelines: {}, activeTurn: null },
                             { type: "message", id: 10, kind: "bot", body: "hi" });
    expect(other.messages[0].live).toBeUndefined();
  });
});
