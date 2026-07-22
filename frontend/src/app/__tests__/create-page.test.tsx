import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import CreateRoom from "../create/page";
import { getProfile, saveProfile } from "@/lib/rooms-store";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const signIn = vi.fn();
vi.mock("@/lib/session", () => ({ useSession: () => ({ signIn }) }));

const createRoom = vi.fn();
vi.mock("@/lib/api", () => ({ createRoom: (...a: any[]) => createRoom(...a) }));

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});

describe("CreateRoom page", () => {
  it("prefills member fields from the saved profile", async () => {
    saveProfile({ nickname: "an", display_name: "An", pin: "1234", bank_code: "VCB" });
    render(<CreateRoom />);
    await waitFor(() =>
      expect(screen.getByLabelText("Nickname")).toHaveValue("an"));
    expect(screen.getByLabelText("Display name")).toHaveValue("An");
    expect(screen.getByLabelText("PIN")).toHaveValue("1234");
    expect(screen.getByLabelText("Bank code")).toHaveValue("VCB");
    expect(screen.getByLabelText("Room name")).toHaveValue("");
  });

  it("creates the room, saves the profile back, signs in, and goes home", async () => {
    createRoom.mockResolvedValue({
      token: "tok", room_id: 9, room_name: "Team", member_id: 1, invite_token: "iv",
    });
    render(<CreateRoom />);
    fireEvent.change(screen.getByLabelText("Room name"), { target: { value: "Team" } });
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "An" } });
    fireEvent.change(screen.getByLabelText("Nickname"), { target: { value: "an" } });
    fireEvent.change(screen.getByLabelText("PIN"), { target: { value: "1234" } });
    fireEvent.click(screen.getByRole("button", { name: /create room/i }));

    await waitFor(() => expect(signIn).toHaveBeenCalledWith("tok", 9, "Team"));
    expect(createRoom).toHaveBeenCalledWith(expect.objectContaining({
      room_name: "Team", nickname: "an", pin: "1234",
    }));
    expect(getProfile()).toMatchObject({ nickname: "an", pin: "1234", display_name: "An" });
    expect(push).toHaveBeenCalledWith("/");
  });

  it("shows the server error message on failure", async () => {
    createRoom.mockRejectedValue(new Error("Nickname and PIN are required."));
    render(<CreateRoom />);
    fireEvent.click(screen.getByRole("button", { name: /create room/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Nickname and PIN are required.");
    expect(signIn).not.toHaveBeenCalled();
  });
});
