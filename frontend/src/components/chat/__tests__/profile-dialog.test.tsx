import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ProfileDialog } from "../room-view";
import { getProfile } from "@/lib/rooms-store";

vi.mock("@/lib/session", () => ({ useSession: () => ({ signOut: vi.fn(), memberId: 1 }) }));

const updateMe = vi.fn();
// The dialog now also renders MemberNotes, which reads the knowledge snapshot.
vi.mock("@/lib/api", () => ({
  updateMe: (...a: any[]) => updateMe(...a),
  getKnowledge: () => Promise.resolve({ observations: [], subjects: [], etags: {} }),
}));

const member = {
  id: 1, display_name: "An", nickname: "an", claimed: true, has_bank: false,
  bank_code: "", account_number: "", account_holder: "",
} as any;

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});

describe("ProfileDialog save-back", () => {
  it("writes edited fields to the saved profile after a successful save", async () => {
    updateMe.mockResolvedValue({ ok: true });
    render(<ProfileDialog member={member} roomId={3} knowledgeVersion={0} onClose={() => {}} onSaved={() => {}} />);
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "An Nguyen" } });
    fireEvent.change(screen.getByLabelText("Bank code"), { target: { value: "VCB" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.getByText("Saved")).toBeInTheDocument());
    expect(getProfile()).toMatchObject({ display_name: "An Nguyen", bank_code: "VCB" });
  });

  it("does not touch the saved profile when the save fails", async () => {
    updateMe.mockRejectedValue(new Error("boom"));
    render(<ProfileDialog member={member} roomId={3} knowledgeVersion={0} onClose={() => {}} onSaved={() => {}} />);
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "X" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(getProfile()).toEqual({});
  });

  it("defaults the random-pick toggle to checked and sends false when unchecked", async () => {
    updateMe.mockResolvedValue({ ok: true });
    render(<ProfileDialog member={member} roomId={3} knowledgeVersion={0} onClose={() => {}} onSaved={() => {}} />);
    const toggle = screen.getByRole("checkbox", { name: /include me in random picks/i });
    expect(toggle).toBeChecked();

    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(updateMe).toHaveBeenCalled());
    expect(updateMe).toHaveBeenCalledWith(expect.objectContaining({ default_participant: false }));
  });

  it("respects a member already opted out of draws", () => {
    render(
      <ProfileDialog
        member={{ ...member, default_participant: false }}
        roomId={3}
        knowledgeVersion={0}
        onClose={() => {}}
        onSaved={() => {}}
      />,
    );
    expect(screen.getByRole("checkbox", { name: /include me in random picks/i })).not.toBeChecked();
  });
});
