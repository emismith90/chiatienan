import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as api from "@/lib/api";
import { MemberNotes } from "../member-notes";
import { knowledge } from "./knowledge-fixture";

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "getKnowledge").mockResolvedValue(structuredClone(knowledge));
});

describe("MemberNotes", () => {
  it("shows what the bot remembers about this person, with the panel's own rows", async () => {
    render(<MemberNotes roomId={3} memberId={9} version={0} />);
    await waitFor(() =>
      expect(screen.getByText("Đề xuất quán rồi lại đổi ý.")).toBeInTheDocument());
    expect(screen.getByText("What Phoenix remembers")).toBeInTheDocument();
    // Same NoteRow as the panel, so the staleness marker comes along for free.
    expect(screen.getByText(/the bot stopped reading this/)).toBeInTheDocument();
  });

  it("matches on the member id, not on a name that happens to look the same", async () => {
    // Member 1 has no notes; member 9's must not leak in on a label match.
    render(<MemberNotes roomId={3} memberId={1} version={0} />);
    await waitFor(() => expect(screen.getByText("No notes yet.")).toBeInTheDocument());
    expect(screen.queryByText("Đề xuất quán rồi lại đổi ý.")).not.toBeInTheDocument();
  });

  it("never shows a place note here", async () => {
    render(<MemberNotes roomId={3} memberId={9} version={0} />);
    await waitFor(() =>
      expect(screen.getByText("Đề xuất quán rồi lại đổi ý.")).toBeInTheDocument());
    expect(screen.queryByText(/Phải gọi trước/)).not.toBeInTheDocument();
  });

  it("opens the same editor as the panel, pre-filed against this person", async () => {
    render(<MemberNotes roomId={3} memberId={9} version={0} />);
    await waitFor(() =>
      expect(screen.getByText("Đề xuất quán rồi lại đổi ý.")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Add note/ }));
    expect(screen.getByRole("dialog", { name: "Add note" })).toBeInTheDocument();
    expect(screen.getByLabelText(/About which place or person/)).toHaveValue("member:nhim");
  });

  it("stays quiet when the member is not in the subject list", async () => {
    render(<MemberNotes roomId={3} memberId={404} version={0} />);
    await waitFor(() => expect(api.getKnowledge).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: /Add note/ })).not.toBeInTheDocument();
  });
});
