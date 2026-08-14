import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as api from "@/lib/api";
import { KnowledgePanel } from "../knowledge-panel";
import { knowledge } from "./knowledge-fixture";

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "getKnowledge").mockResolvedValue(structuredClone(knowledge));
});

const openPanel = () => render(<KnowledgePanel roomId={3} version={0} active={true} />);

describe("KnowledgePanel — places", () => {
  it("opens on the places tab and states the ledger's own counts in words", async () => {
    openPanel();
    await waitFor(() => expect(screen.getByText("Quán Bé Bự")).toBeInTheDocument());
    // "4 lần · 12 ngày trước · hay ăn T5 · 2 ghi nhớ" — derived, and read-only.
    expect(screen.getByText(/4 lần · 12 ngày trước/)).toBeInTheDocument();
    expect(screen.getByText(/2 ghi nhớ/)).toBeInTheDocument();
    // The band, not the amount: a suggestion never speaks in VND (design D5).
    expect(screen.getByText("vừa")).toBeInTheDocument();
    expect(screen.queryByText(/55\.000/)).not.toBeInTheDocument();
  });

  it("never offers a computed number as an input", async () => {
    openPanel();
    await waitFor(() => expect(screen.getByText("Quán Bé Bự")).toBeInTheDocument());
    // The only text box on the places tab is the search field.
    const boxes = screen.getAllByRole("searchbox");
    expect(boxes).toHaveLength(1);
    expect(screen.queryByDisplayValue("4")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("12")).not.toBeInTheDocument();
  });

  it("flags a place that is closed, untried, or not walkable", async () => {
    openPanel();
    await waitFor(() => expect(screen.getByText("Cơm gà Thịnh Lơ")).toBeInTheDocument());
    expect(screen.getByText("đóng tới 20/8")).toBeInTheDocument();
    expect(screen.getByText("chưa thử")).toBeInTheDocument();
    expect(screen.getByText("không đi bộ được")).toBeInTheDocument();
  });

  it("filters by name, alias and tag", async () => {
    openPanel();
    await waitFor(() => expect(screen.getByText("Quán Bé Bự")).toBeInTheDocument());
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "bé bự" } });
    expect(screen.getByText("Quán Bé Bự")).toBeInTheDocument();
    expect(screen.queryByText("Cơm gà Thịnh Lơ")).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "chưa-thử" } });
    expect(screen.getByText("Cơm gà Thịnh Lơ")).toBeInTheDocument();
    expect(screen.queryByText("Quán Bé Bự")).not.toBeInTheDocument();
  });

  it("opens the editor when a place is tapped", async () => {
    openPanel();
    await waitFor(() => expect(screen.getByText("Quán Bé Bự")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Quán Bé Bự"));
    expect(screen.getByRole("dialog", { name: /Sửa quán Quán Bé Bự/ })).toBeInTheDocument();
  });
});

describe("KnowledgePanel — notes", () => {
  const goToNotes = async () => {
    openPanel();
    await waitFor(() => expect(screen.getByText("Quán Bé Bự")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: /Ghi nhớ/ }));
  };

  it("groups notes under the thing they are about, rules first", async () => {
    await goToNotes();
    const texts = screen.getAllByRole("button")
      .map((b) => b.textContent ?? "")
      .filter((t) => t.includes("Phải gọi trước") || t.includes("Hôm nay hết gà"));
    expect(texts[0]).toContain("Phải gọi trước");
    expect(screen.getByText("Giang Hoàng", { exact: false })).toBeInTheDocument();
  });

  it("renders a clock gate in human words, never as `order-by@11:30`", async () => {
    await goToNotes();
    expect(screen.getByText("Đặt trước 11:30")).toBeInTheDocument();
    expect(screen.queryByText(/order-by@/)).not.toBeInTheDocument();
  });

  it("marks a standing rule as always true and a dated note with its date", async () => {
    await goToNotes();
    expect(screen.getByText("luôn đúng")).toBeInTheDocument();
    expect(screen.getByText("10/8")).toBeInTheDocument();
  });

  it("says out loud when a note is too old for the bot to read", async () => {
    await goToNotes();
    expect(screen.getByText(/đã cũ, bot không đọc nữa/)).toBeInTheDocument();
  });

  it("opens the note editor on tap", async () => {
    await goToNotes();
    fireEvent.click(screen.getByText("Hôm nay hết gà."));
    expect(screen.getByRole("dialog", { name: "Sửa ghi nhớ" })).toBeInTheDocument();
  });
});

describe("KnowledgePanel — memory log", () => {
  it("shows dated sections with only the newest expanded, and a read-only watermark", async () => {
    openPanel();
    await waitFor(() => expect(screen.getByText("Quán Bé Bự")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: /Nhật ký/ }));

    // Newest open, older collapsed.
    expect(screen.getByText("- Ăn cơm gà")).toBeInTheDocument();
    expect(screen.queryByText(/Ăn bún chả/)).not.toBeInTheDocument();
    expect(screen.getByText(/Đã tóm tắt tới 2026-08-01/)).toBeInTheDocument();
    // The watermark is prose, not a field: rolling it back re-summarizes months
    // of chat into duplicate sections.
    expect(screen.queryByDisplayValue("42")).not.toBeInTheDocument();
  });
});

describe("KnowledgePanel — empty states", () => {
  it("says how each store gets filled rather than 'no data'", async () => {
    vi.spyOn(api, "getKnowledge").mockResolvedValue({
      ...structuredClone(knowledge),
      places: [], observations: [], memory: { sections: [], watermark: { through_id: null, through_at: null } },
    });
    openPanel();
    await waitFor(() =>
      expect(screen.getByText(/nói với @phoenix «thêm quán/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: /Ghi nhớ/ }));
    expect(screen.getByText(/phải gọi trước 11h30/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /Nhật ký/ }));
    expect(screen.getByText(/khi hội thoại cũ hơn 10 tuần/)).toBeInTheDocument();
  });
});

describe("KnowledgePanel — fetching", () => {
  it("does not fetch while the ledger tab is showing", () => {
    render(<KnowledgePanel roomId={3} version={0} active={false} />);
    expect(api.getKnowledge).not.toHaveBeenCalled();
  });

  it("refetches when the room stream reports a change", async () => {
    const { rerender } = render(<KnowledgePanel roomId={3} version={0} active={true} />);
    await waitFor(() => expect(api.getKnowledge).toHaveBeenCalledTimes(1));
    rerender(<KnowledgePanel roomId={3} version={1} active={true} />);
    await waitFor(() => expect(api.getKnowledge).toHaveBeenCalledTimes(2));
  });
});
