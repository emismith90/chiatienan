import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { RecordDraftCard } from "../record-draft-card";

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  commitDraft: vi.fn(() => Promise.resolve({})),
  cancelDraft: vi.fn(() => Promise.resolve({})),
}));
import * as api from "@/lib/api";

/** A `record_draft` as the backend publishes it: each action carries its headline and
 *  the per-field before/after it was computed from. */
const card = (status: string) => ({
  id: 21,
  kind: "record_draft",
  body: "📝 **Confirm these changes?**",
  attachments: {
    type: "record_draft",
    status,
    turn_id: "t-1",
    actions: [
      { op: "delete", soft: true, headline: "Hide place «Bún bò Huế 1992»",
        changes: [{ field: "active", before: true, after: false }] },
      { op: "update", headline: "Change place «Phở Vui»",
        changes: [{ field: "phone", before: null, after: "0901" },
                  { field: "aliases", before: ["vui"], after: ["vui", "pho vui"] }] },
      { op: "create", headline: "Add place «Cơm rang Tuấn»",
        changes: [{ field: "name", before: null, after: "Cơm rang Tuấn" }] },
    ],
  },
});

describe("RecordDraftCard", () => {
  it("shows every change, before → after, and confirms through the generic draft route", () => {
    render(<RecordDraftCard message={card("pending")} roomId={3} />);
    expect(screen.getByText("Confirm these 3 changes?")).toBeInTheDocument();
    expect(screen.getByText("Hide place «Bún bò Huế 1992»")).toBeInTheDocument();
    expect(screen.getByText("0901")).toBeInTheDocument();
    expect(screen.getByText("vui, pho vui")).toBeInTheDocument();
    expect(screen.getByText("Cơm rang Tuấn")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(api.commitDraft).toHaveBeenCalledWith(3, 21);
  });

  it("dismisses through the generic draft route", () => {
    render(<RecordDraftCard message={card("pending")} roomId={3} />);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(api.cancelDraft).toHaveBeenCalledWith(3, 21);
  });

  it("drops the buttons once decided", () => {
    render(<RecordDraftCard message={card("committed")} roomId={3} />);
    expect(screen.getByText("Saved")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
  });
});
