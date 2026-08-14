import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import { PlaceDialog } from "../place-dialog";
import { knowledge } from "./knowledge-fixture";

const place = knowledge.places[0];
const hidden = { ...knowledge.places[0], active: false };

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "patchPlace").mockResolvedValue({ ok: true, changed: true } as any);
  vi.spyOn(api, "createPlace").mockResolvedValue({ ok: true } as any);
  vi.spyOn(api, "deletePlace").mockResolvedValue({ ok: true } as any);
  vi.spyOn(api, "renamePlaceSlug").mockResolvedValue({ ok: true } as any);
});

describe("PlaceDialog", () => {
  it("round-trips the editable fields", async () => {
    const onSaved = vi.fn();
    render(<PlaceDialog roomId={3} place={place} onClose={() => {}} onSaved={onSaved} />);

    expect(screen.getByLabelText("Name")).toHaveValue("Quán Bé Bự");
    expect(screen.getByLabelText(/Other names/)).toHaveValue("bé bự");
    expect(screen.getByLabelText("Phone")).toHaveValue("0912345678");

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Bé Bự (mới)" } });
    fireEvent.change(screen.getByLabelText("Tags"), { target: { value: "cơm, nhanh" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(api.patchPlace).toHaveBeenCalledWith(3, 1, expect.objectContaining({
      name: "Bé Bự (mới)", tags: ["cơm", "nhanh"], phone: "0912345678",
    }));
  });

  it("states the slug, and keeps it out of the form Save touches", () => {
    render(<PlaceDialog roomId={3} place={place} onClose={() => {}} onSaved={() => {}} />);
    // Renaming is a migration, so it must never ride along with a phone-number
    // edit: the identity is shown, and the field list does not contain it.
    expect(screen.getByText(/quan-be-bu/)).toBeInTheDocument();
    expect(screen.queryByDisplayValue("quan-be-bu")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("New identifier")).not.toBeInTheDocument();
  });

  it("saving an edited name never sends a slug", async () => {
    render(<PlaceDialog roomId={3} place={place} onClose={() => {}} onSaved={() => {}} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Bé Bự" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.patchPlace).toHaveBeenCalled());
    expect((api.patchPlace as any).mock.calls[0][2]).not.toHaveProperty("slug");
  });

  it("shows the ledger's numbers as prose, labelled as coming from the ledger", () => {
    render(<PlaceDialog roomId={3} place={place} onClose={() => {}} onSaved={() => {}} />);
    expect(screen.getByText(/4 visits · 12d ago/)).toBeInTheDocument();
    expect(screen.getByText(/55.000₫\/head — from the ledger/)).toBeInTheDocument();
  });

  it("clears an emptied optional number rather than sending it as 0", async () => {
    const withWalk = { ...place, walk_minutes: 9 };
    render(<PlaceDialog roomId={3} place={withWalk} onClose={() => {}} onSaved={() => {}} />);
    fireEvent.change(screen.getByLabelText("Walk minutes"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.patchPlace).toHaveBeenCalled());
    expect(api.patchPlace).toHaveBeenCalledWith(3, 1,
      expect.objectContaining({ walk_minutes: null }));
  });

  it("refuses to save an empty name without calling the API", () => {
    render(<PlaceDialog roomId={3} place={place} onClose={() => {}} onSaved={() => {}} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "  " } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByText("A place needs a name.")).toBeInTheDocument();
    expect(api.patchPlace).not.toHaveBeenCalled();
  });

  it("hides rather than deletes, and confirms first", async () => {
    render(<PlaceDialog roomId={3} place={place} onClose={() => {}} onSaved={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Hide this place" }));
    expect(screen.getByText(/Recorded meals stay as they are/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Hide" }));
    await waitFor(() => expect(api.deletePlace).toHaveBeenCalledWith(3, 1));
  });

  it("offers to bring a hidden place back", async () => {
    render(<PlaceDialog roomId={3} place={hidden} onClose={() => {}} onSaved={() => {}} />);
    expect(screen.queryByRole("button", { name: "Hide this place" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Unhide this place" }));
    await waitFor(() => expect(api.patchPlace).toHaveBeenCalledWith(3, 1, { active: true }));
  });

  it("creates a new place when opened with none", async () => {
    render(<PlaceDialog roomId={3} place={null} onClose={() => {}} onSaved={() => {}} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Bún đậu Cô Tư" } });
    fireEvent.click(screen.getByRole("button", { name: "Add place" }));
    await waitFor(() => expect(api.createPlace).toHaveBeenCalledWith(3,
      expect.objectContaining({ name: "Bún đậu Cô Tư" })));
  });

  it("surfaces the server's own collision message, not a generic conflict line", async () => {
    // A place 409 means "that name is taken", not "someone else edited this" —
    // the etag story belongs to the two file-backed stores only.
    const { ApiError } = await import("@/lib/api");
    vi.spyOn(api, "createPlace").mockRejectedValue(
      new ApiError(409, "«Quán Bé Bự» is already on the list."));
    render(<PlaceDialog roomId={3} place={null} onClose={() => {}} onSaved={() => {}} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "quán bé bự" } });
    fireEvent.click(screen.getByRole("button", { name: "Add place" }));
    await waitFor(() =>
      expect(screen.getByText("«Quán Bé Bự» is already on the list.")).toBeInTheDocument());
    expect(screen.queryByText(/reloaded/)).not.toBeInTheDocument();
  });
});

describe("PlaceDialog — changing the identifier", () => {
  function open() {
    render(<PlaceDialog roomId={3} place={place} onClose={() => {}} onSaved={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "change" }));
  }

  it("says what will move before it moves, in numbers", () => {
    // The notes are the whole reason the slug was frozen; a confirmation that
    // does not say how many are at stake is not a confirmation.
    open();
    expect(screen.getByText(/2 notes and 1 pending card will move/)).toBeInTheDocument();
  });

  it("names the seed files rather than leaving them as a surprise", () => {
    open();
    expect(screen.getByText(/seed files still name the old one/)).toBeInTheDocument();
  });

  it("pre-fills the current slug and sends what was typed, unnormalised", async () => {
    // No client-side slugify: `roster._fold` hand-maps đ→d because NFD leaves it
    // whole, and a second implementation here could disagree with the real one.
    (api.renamePlaceSlug as any).mockResolvedValue({
      ok: true, changed: true, slug: "bun-cha-huong-lien", former_slug: "quan-be-bu",
      notes_moved: 2, notes_deduped: 0, memos_moved: 1,
    });
    open();
    const field = screen.getByLabelText("New identifier");
    expect(field).toHaveValue("quan-be-bu");
    fireEvent.change(field, { target: { value: "  Bún Chả Hương Liên  " } });
    fireEvent.click(screen.getByRole("button", { name: "Change identifier" }));

    await waitFor(() => expect(api.renamePlaceSlug).toHaveBeenCalledWith(
      3, 1, "  Bún Chả Hương Liên  "));
  });

  it("shows the slug the server actually applied", async () => {
    (api.renamePlaceSlug as any).mockResolvedValue({
      ok: true, changed: true, slug: "bun-cha-huong-lien", former_slug: "quan-be-bu",
      notes_moved: 2, notes_deduped: 0, memos_moved: 1,
    });
    open();
    fireEvent.change(screen.getByLabelText("New identifier"),
                     { target: { value: "Bún Chả Hương Liên" } });
    fireEvent.click(screen.getByRole("button", { name: "Change identifier" }));

    await waitFor(() =>
      expect(screen.getByText(/bun-cha-huong-lien/)).toBeInTheDocument());
    expect(screen.queryByLabelText("New identifier")).not.toBeInTheDocument();
  });

  it("shows a collision as the server described it, and stays open", async () => {
    // A place 409 is "that identifier is taken", not a stale etag — it must not
    // trigger the reload banner, and the user needs the box back to try again.
    (api.renamePlaceSlug as any).mockRejectedValue(
      new ApiError(409, "«Bún chả Hương Liên» already uses «bun-cha-huong-lien»."));
    open();
    fireEvent.change(screen.getByLabelText("New identifier"),
                     { target: { value: "bun-cha-huong-lien" } });
    fireEvent.click(screen.getByRole("button", { name: "Change identifier" }));

    await waitFor(() => expect(
      screen.getByText(/already uses/)).toBeInTheDocument());
    expect(screen.getByLabelText("New identifier")).toBeInTheDocument();
  });

  it("does not fire on an empty identifier", () => {
    open();
    fireEvent.change(screen.getByLabelText("New identifier"), { target: { value: "  " } });
    fireEvent.click(screen.getByRole("button", { name: "Change identifier" }));
    expect(api.renamePlaceSlug).not.toHaveBeenCalled();
  });

  it("is not offered when creating a place", () => {
    render(<PlaceDialog roomId={3} place={null} onClose={() => {}} onSaved={() => {}} />);
    expect(screen.queryByRole("button", { name: "change" })).not.toBeInTheDocument();
  });
});
