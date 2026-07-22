import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { RoomSwitcher } from "../room-switcher";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const session = {
  rooms: [] as any[], roomId: 1 as number | null, roomName: "Lunch A",
  switchRoom: vi.fn(), removeRoom: vi.fn(),
};
vi.mock("@/lib/session", () => ({ useSession: () => session }));

const oneRoom = [{ roomId: 1, roomName: "Lunch A", token: "t1", lastAccessAt: 2 }];
const twoRooms = [
  ...oneRoom,
  { roomId: 2, roomName: "Lunch B", token: "t2", lastAccessAt: 1 },
];

beforeEach(() => {
  vi.clearAllMocks();
  session.rooms = oneRoom;
  session.roomId = 1;
  session.roomName = "Lunch A";
});

describe("RoomSwitcher", () => {
  it("shows the active room name and opens the menu", () => {
    render(<RoomSwitcher />);
    const btn = screen.getByRole("button", { name: /room menu/i });
    expect(btn).toHaveTextContent("Lunch A");
    fireEvent.click(btn);
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("hides the switch list with a single room but still offers create/remove", () => {
    render(<RoomSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: /room menu/i }));
    expect(screen.queryByText("Switch room")).not.toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /create a room/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /remove from this device/i })).toBeInTheDocument();
  });

  it("lists other rooms when there are several and switches on tap", () => {
    session.rooms = twoRooms;
    render(<RoomSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: /room menu/i }));
    expect(screen.getByText("Switch room")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: "Lunch B" }));
    expect(session.switchRoom).toHaveBeenCalledWith(2);
  });

  it("navigates to /create from the menu", () => {
    render(<RoomSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: /room menu/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /create a room/i }));
    expect(push).toHaveBeenCalledWith("/create");
  });

  it("navigates to /join from the menu", () => {
    render(<RoomSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: /room menu/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /join a room/i }));
    expect(push).toHaveBeenCalledWith("/join");
  });

  it("removes the current room only after confirmation", () => {
    render(<RoomSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: /room menu/i }));
    vi.spyOn(window, "confirm").mockReturnValueOnce(false);
    fireEvent.click(screen.getByRole("menuitem", { name: /remove from this device/i }));
    expect(session.removeRoom).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /room menu/i }));
    vi.spyOn(window, "confirm").mockReturnValueOnce(true);
    fireEvent.click(screen.getByRole("menuitem", { name: /remove from this device/i }));
    expect(session.removeRoom).toHaveBeenCalledWith(1);
  });
});
