import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import Join from "../join/[token]/page";
import { getProfile, saveProfile, upsertRoom } from "@/lib/rooms-store";

const push = vi.fn();
const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace }),
  useParams: () => ({ token: "invite-1" }),
}));

const signIn = vi.fn();
const switchRoom = vi.fn();
vi.mock("@/lib/session", () => ({ useSession: () => ({ signIn, switchRoom }) }));

const roomInfo = vi.fn();
const createAccount = vi.fn();
const identify = vi.fn();
vi.mock("@/lib/api", () => ({
  roomInfo: (...a: any[]) => roomInfo(...a),
  createAccount: (...a: any[]) => createAccount(...a),
  identify: (...a: any[]) => identify(...a),
}));

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  roomInfo.mockResolvedValue({ room_id: 5, name: "Lunch B", members: [] });
});

describe("Join page (multi-room)", () => {
  it("short-circuits to the room when this device already holds its token", async () => {
    upsertRoom({ roomId: 5, roomName: "Lunch B", token: "have-it" });
    render(<Join />);
    await waitFor(() => expect(switchRoom).toHaveBeenCalledWith(5));
    expect(replace).toHaveBeenCalledWith("/");
    expect(signIn).not.toHaveBeenCalled();
  });

  it("prefills the form from the saved profile", async () => {
    saveProfile({ nickname: "an", pin: "1234", display_name: "An", bank_code: "VCB" });
    render(<Join />);
    await waitFor(() => expect(screen.getByLabelText("Nickname")).toHaveValue("an"));
    expect(screen.getByLabelText("PIN")).toHaveValue("1234");
  });

  it("passes the room name to signIn and saves the profile back after joining", async () => {
    createAccount.mockResolvedValue({ token: "tok5", room_id: 5, member_id: 3 });
    render(<Join />);
    await screen.findByText(/Join/);
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "An" } });
    fireEvent.change(screen.getByLabelText("Nickname"), { target: { value: "an" } });
    fireEvent.change(screen.getByLabelText("PIN"), { target: { value: "9999" } });
    fireEvent.click(screen.getByRole("button", { name: /create & join/i }));

    await waitFor(() => expect(signIn).toHaveBeenCalledWith("tok5", 5, "Lunch B"));
    expect(getProfile()).toMatchObject({ nickname: "an", pin: "9999", display_name: "An" });
    expect(push).toHaveBeenCalledWith("/");
  });

  it("saves nickname and PIN back after sign-in mode too", async () => {
    identify.mockResolvedValue({ token: "tok5", room_id: 5 });
    render(<Join />);
    await screen.findByText(/Join/);
    fireEvent.change(screen.getByLabelText("Nickname"), { target: { value: "binh" } });
    fireEvent.change(screen.getByLabelText("PIN"), { target: { value: "1111" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(signIn).toHaveBeenCalledWith("tok5", 5, "Lunch B"));
    expect(getProfile()).toMatchObject({ nickname: "binh", pin: "1111" });
  });
});
