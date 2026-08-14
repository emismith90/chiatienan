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
    expect(screen.getByRole("button", { name: "Standing rule" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/never drops it for being old/)).toBeInTheDocument();
    // A rule has no date, so no date field is offered.
    expect(screen.queryByLabelText("Date")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "One day" }));
    expect(screen.getByText(/after 6 months the bot stops reading it/)).toBeInTheDocument();
    expect(screen.getByLabelText("Date")).toBeInTheDocument();
  });

  it("sends when=null for a rule and a date for an observation", async () => {
    const p = open(dated);
    fireEvent.click(screen.getByRole("button", { name: "Standing rule" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.patchNote).toHaveBeenCalled());
    expect(api.patchNote).toHaveBeenCalledWith(3, "bbb222",
      expect.objectContaining({ standing: true, when: null, etag: "obs-etag-1" }));
    expect(p.onSaved).toHaveBeenCalled();
  });
});

describe("NoteDialog — clock gates", () => {
  it("offers the three verbs as a picker, never the `kind@HH:MM` syntax", () => {
    open(rule);
    const select = screen.getByLabelText("Clock rule");
    expect(select).toHaveValue("order-by");
    expect(screen.getByRole("option", { name: "Order by…" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Busy from…" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Closes…" })).toBeInTheDocument();
    // The stored time is prefilled from the gate, split off the `@`.
    expect(screen.getByLabelText("At")).toHaveValue("11:30");
  });

  it("clears the gate when 'None' is chosen", async () => {
    open(rule);
    fireEvent.change(screen.getByLabelText("Clock rule"), { target: { value: "" } });
    expect(screen.queryByLabelText("At")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.patchNote).toHaveBeenCalledWith(3, "aaa111",
      expect.objectContaining({ gate_kind: null, gate_at: null })));
  });

  it("sends the picked verb and time together", async () => {
    open(dated);
    fireEvent.change(screen.getByLabelText("Clock rule"), { target: { value: "closes" } });
    fireEvent.change(screen.getByLabelText("At"), { target: { value: "12:45" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.patchNote).toHaveBeenCalledWith(3, "bbb222",
      expect.objectContaining({ gate_kind: "closes", gate_at: "12:45" })));
  });
});

describe("NoteDialog — subject", () => {
  it("cannot be changed on an existing note, and says why", () => {
    open(rule);
    expect(screen.getByText(/delete this and add it again/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/About which place or person/)).not.toBeInTheDocument();
  });

  it("is a grouped picker when adding, prefilled from where you started", () => {
    open(null as any, { note: null, presetSubject: "member:nhim" });
    const select = screen.getByLabelText(/About which place or person/);
    expect(select).toHaveValue("member:nhim");
    expect(screen.getByRole("group", { name: "Places" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "People" })).toBeInTheDocument();
  });

  it("creates against the chosen subject", async () => {
    open(null as any, { note: null });
    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "Hết chỗ ngồi." } });
    fireEvent.click(screen.getByRole("button", { name: "Remember" }));
    await waitFor(() => expect(api.createNote).toHaveBeenCalledWith(3,
      expect.objectContaining({ subject: "place:quan-be-bu", text: "Hết chỗ ngồi." })));
  });
});

describe("NoteDialog — delete and conflict", () => {
  it("confirms before deleting", async () => {
    open(rule);
    fireEvent.click(screen.getByRole("button", { name: "Delete this note" }));
    expect(screen.getByText("Delete this note for good?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() =>
      expect(api.deleteNote).toHaveBeenCalledWith(3, "aaa111", "obs-etag-1"));
  });

  it("reloads on a stale-etag conflict instead of retrying blind", async () => {
    const { ApiError } = await import("@/lib/api");
    vi.spyOn(api, "patchNote").mockRejectedValue(
      new ApiError(409, "Someone just changed the notes — reload."));
    const p = open(rule);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(p.onConflict).toHaveBeenCalled());
    expect(screen.getByText(/reload/)).toBeInTheDocument();
    expect(p.onSaved).not.toHaveBeenCalled();
  });

  it("refuses an empty note without calling the API", () => {
    open(rule);
    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByText("A note needs some text.")).toBeInTheDocument();
    expect(api.patchNote).not.toHaveBeenCalled();
  });
});
