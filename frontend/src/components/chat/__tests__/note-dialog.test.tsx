import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as api from "@/lib/api";
import { NoteDialog } from "../note-dialog";
import { knowledge } from "./knowledge-fixture";

const rule = knowledge.observations[0];     // standing + order-by@11:30
const dated = knowledge.observations[1];    // 2026-08-10, no gate

function open(note = rule, extra: Partial<React.ComponentProps<typeof NoteDialog>> = {}) {
  const props = {
    roomId: 3, note, subjects: knowledge.subjects, etag: "obs-etag-1",
    onClose: vi.fn(), onSaved: vi.fn(), onConflict: vi.fn(), ...extra,
  };
  render(<NoteDialog {...props} />);
  return props;
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "patchNote").mockResolvedValue({ ok: true } as any);
  vi.spyOn(api, "createNote").mockResolvedValue({ ok: true } as any);
  vi.spyOn(api, "deleteNote").mockResolvedValue({ ok: true } as any);
});

describe("NoteDialog — the standing/dated choice", () => {
  it("spells out what each kind means, because it decides whether the note decays", () => {
    open(rule);
    expect(screen.getByRole("button", { name: "Quy tắc" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/không bao giờ bỏ qua vì cũ/)).toBeInTheDocument();
    // A rule has no date, so no date field is offered.
    expect(screen.queryByLabelText("Ngày")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Chuyện một hôm" }));
    expect(screen.getByText(/sau 6 tháng bot không đọc nữa/)).toBeInTheDocument();
    expect(screen.getByLabelText("Ngày")).toBeInTheDocument();
  });

  it("sends when=null for a rule and a date for an observation", async () => {
    const p = open(dated);
    fireEvent.click(screen.getByRole("button", { name: "Quy tắc" }));
    fireEvent.click(screen.getByRole("button", { name: "Lưu" }));
    await waitFor(() => expect(api.patchNote).toHaveBeenCalled());
    expect(api.patchNote).toHaveBeenCalledWith(3, "bbb222",
      expect.objectContaining({ standing: true, when: null, etag: "obs-etag-1" }));
    expect(p.onSaved).toHaveBeenCalled();
  });
});

describe("NoteDialog — clock gates", () => {
  it("offers the three verbs as a picker, never the `kind@HH:MM` syntax", () => {
    open(rule);
    const select = screen.getByLabelText("Điều kiện giờ");
    expect(select).toHaveValue("order-by");
    expect(screen.getByRole("option", { name: "Đặt trước…" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Đông từ…" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Đóng cửa…" })).toBeInTheDocument();
    // The stored time is prefilled from the gate, split off the `@`.
    expect(screen.getByLabelText("Lúc")).toHaveValue("11:30");
  });

  it("clears the gate when 'Không có' is chosen", async () => {
    open(rule);
    fireEvent.change(screen.getByLabelText("Điều kiện giờ"), { target: { value: "" } });
    expect(screen.queryByLabelText("Lúc")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Lưu" }));
    await waitFor(() => expect(api.patchNote).toHaveBeenCalledWith(3, "aaa111",
      expect.objectContaining({ gate_kind: null, gate_at: null })));
  });

  it("sends the picked verb and time together", async () => {
    open(dated);
    fireEvent.change(screen.getByLabelText("Điều kiện giờ"), { target: { value: "closes" } });
    fireEvent.change(screen.getByLabelText("Lúc"), { target: { value: "12:45" } });
    fireEvent.click(screen.getByRole("button", { name: "Lưu" }));
    await waitFor(() => expect(api.patchNote).toHaveBeenCalledWith(3, "bbb222",
      expect.objectContaining({ gate_kind: "closes", gate_at: "12:45" })));
  });
});

describe("NoteDialog — subject", () => {
  it("cannot be changed on an existing note, and says why", () => {
    open(rule);
    expect(screen.getByText(/xoá rồi thêm lại/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Về ai \/ quán nào/)).not.toBeInTheDocument();
  });

  it("is a grouped picker when adding, prefilled from where you started", () => {
    open(null as any, { note: null, presetSubject: "member:nhim" });
    const select = screen.getByLabelText(/Về ai \/ quán nào/);
    expect(select).toHaveValue("member:nhim");
    expect(screen.getByRole("group", { name: "Quán" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Người" })).toBeInTheDocument();
  });

  it("creates against the chosen subject", async () => {
    open(null as any, { note: null });
    fireEvent.change(screen.getByLabelText("Nội dung"), { target: { value: "Hết chỗ ngồi." } });
    fireEvent.click(screen.getByRole("button", { name: "Ghi nhớ" }));
    await waitFor(() => expect(api.createNote).toHaveBeenCalledWith(3,
      expect.objectContaining({ subject: "place:quan-be-bu", text: "Hết chỗ ngồi." })));
  });
});

describe("NoteDialog — delete and conflict", () => {
  it("confirms before deleting", async () => {
    open(rule);
    fireEvent.click(screen.getByRole("button", { name: "Xoá ghi nhớ này" }));
    expect(screen.getByText("Xoá hẳn ghi nhớ này?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Xoá" }));
    await waitFor(() =>
      expect(api.deleteNote).toHaveBeenCalledWith(3, "aaa111", "obs-etag-1"));
  });

  it("reloads on a stale-etag conflict instead of retrying blind", async () => {
    const { ApiError } = await import("@/lib/api");
    vi.spyOn(api, "patchNote").mockRejectedValue(
      new ApiError(409, "Có người vừa sửa ghi nhớ — hãy tải lại."));
    const p = open(rule);
    fireEvent.click(screen.getByRole("button", { name: "Lưu" }));
    await waitFor(() => expect(p.onConflict).toHaveBeenCalled());
    expect(screen.getByText(/hãy tải lại/)).toBeInTheDocument();
    expect(p.onSaved).not.toHaveBeenCalled();
  });

  it("refuses an empty note without calling the API", () => {
    open(rule);
    fireEvent.change(screen.getByLabelText("Nội dung"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Lưu" }));
    expect(screen.getByText("Ghi nhớ cần có nội dung.")).toBeInTheDocument();
    expect(api.patchNote).not.toHaveBeenCalled();
  });
});
