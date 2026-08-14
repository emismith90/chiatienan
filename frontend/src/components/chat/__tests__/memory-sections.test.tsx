import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as api from "@/lib/api";
import { MemorySections } from "../memory-sections";
import { knowledge } from "./knowledge-fixture";

function open(memory = knowledge.memory) {
  const props = {
    roomId: 3, memory, etag: "mem-etag-1",
    onSaved: vi.fn(), onConflict: vi.fn(),
  };
  render(<MemorySections {...props} />);
  return props;
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "patchMemorySection").mockResolvedValue({ ok: true } as any);
  vi.spyOn(api, "deleteMemorySection").mockResolvedValue({ ok: true } as any);
});

describe("MemorySections", () => {
  it("collapses everything but the newest section", () => {
    open();
    expect(screen.getByText("- Ăn cơm gà")).toBeInTheDocument();
    expect(screen.queryByText(/Ăn bún chả/)).not.toBeInTheDocument();
    // Both headers are there, with their dates.
    expect(screen.getByText("1/7")).toBeInTheDocument();
    expect(screen.getByText("1/8")).toBeInTheDocument();
  });

  it("expands an older section on tap", () => {
    open();
    fireEvent.click(screen.getByText("1/7"));
    expect(screen.getByText(/Ăn bún chả/)).toBeInTheDocument();
  });

  it("edits one section's body against the etag", async () => {
    const p = open();
    fireEvent.click(screen.getByRole("button", { name: "Sửa" }));
    fireEvent.change(screen.getByLabelText(/Sửa mục/), { target: { value: "- Đã sửa" } });
    fireEvent.click(screen.getByRole("button", { name: "Lưu" }));
    await waitFor(() =>
      expect(api.patchMemorySection).toHaveBeenCalledWith(3, 1, "mem-etag-1", "- Đã sửa"));
    expect(p.onSaved).toHaveBeenCalled();
  });

  it("cancels an edit without saving", () => {
    open();
    fireEvent.click(screen.getByRole("button", { name: "Sửa" }));
    fireEvent.change(screen.getByLabelText(/Sửa mục/), { target: { value: "- Nhầm" } });
    fireEvent.click(screen.getByRole("button", { name: "Huỷ" }));
    expect(api.patchMemorySection).not.toHaveBeenCalled();
    expect(screen.getByText("- Ăn cơm gà")).toBeInTheDocument();
  });

  it("confirms before deleting a section", async () => {
    open();
    fireEvent.click(screen.getByRole("button", { name: "Xoá" }));
    fireEvent.click(screen.getByRole("button", { name: "Xoá thật?" }));
    await waitFor(() =>
      expect(api.deleteMemorySection).toHaveBeenCalledWith(3, 1, "mem-etag-1"));
  });

  it("shows the watermark as prose with no control on it", () => {
    open();
    expect(screen.getByText(/Đã tóm tắt tới 2026-08-01/)).toBeInTheDocument();
    // Rolling the watermark back would make the next turn re-summarize months of
    // chat into duplicate sections — so it is never a field.
    expect(screen.queryByDisplayValue("42")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/tóm tắt tới/)).not.toBeInTheDocument();
  });

  it("reloads on a stale-etag conflict", async () => {
    const { ApiError } = await import("@/lib/api");
    vi.spyOn(api, "patchMemorySection").mockRejectedValue(
      new ApiError(409, "Có người vừa sửa nhật ký — hãy tải lại."));
    const p = open();
    fireEvent.click(screen.getByRole("button", { name: "Sửa" }));
    fireEvent.click(screen.getByRole("button", { name: "Lưu" }));
    await waitFor(() => expect(p.onConflict).toHaveBeenCalled());
    expect(screen.getByText(/hãy tải lại/)).toBeInTheDocument();
  });

  it("says how the log gets written when there is none", () => {
    open({ sections: [], watermark: { through_id: null, through_at: null } });
    expect(screen.getByText(/khi hội thoại cũ hơn 10 tuần/)).toBeInTheDocument();
  });
});
