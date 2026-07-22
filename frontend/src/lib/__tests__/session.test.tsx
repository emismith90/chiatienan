import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { SessionProvider, useSession } from "../session";
import { listRooms, upsertRoom } from "../rooms-store";

vi.mock("../api", () => ({
  getMe: vi.fn().mockResolvedValue({ id: 7 }),
  getInvite: vi.fn().mockRejectedValue(new Error("offline")),
  roomInfo: vi.fn(),
}));

function Probe() {
  const s = useSession();
  if (!s.ready) return null;
  return (
    <div>
      <span data-testid="room">{s.roomId ?? "none"}</span>
      <span data-testid="name">{s.roomName}</span>
      <span data-testid="count">{s.rooms.length}</span>
      <button onClick={() => s.signIn("t3", 3, "C")}>in</button>
      <button onClick={() => s.switchRoom(1)}>sw</button>
      <button onClick={() => s.signOut()}>out</button>
    </div>
  );
}

const setup = () => render(<SessionProvider><Probe /></SessionProvider>);

beforeEach(() => localStorage.clear());

describe("SessionProvider", () => {
  it("shows no room when storage is empty", async () => {
    setup();
    expect(await screen.findByTestId("room")).toHaveTextContent("none");
  });

  it("migrates legacy keys on mount", async () => {
    localStorage.setItem("chiatienan.token", "legacy");
    localStorage.setItem("chiatienan.room_id", "9");
    setup();
    expect(await screen.findByTestId("room")).toHaveTextContent("9");
    expect(localStorage.getItem("chiatienan.token")).toBeNull();
  });

  it("signIn adds a room and makes it active; switchRoom moves the pointer; signOut evicts with fallback", async () => {
    upsertRoom({ roomId: 1, roomName: "A", token: "t1" });
    setup();
    expect(await screen.findByTestId("room")).toHaveTextContent("1");

    act(() => screen.getByText("in").click());
    expect(screen.getByTestId("room")).toHaveTextContent("3");
    expect(screen.getByTestId("name")).toHaveTextContent("C");
    expect(screen.getByTestId("count")).toHaveTextContent("2");

    act(() => screen.getByText("sw").click());
    expect(screen.getByTestId("room")).toHaveTextContent("1");

    act(() => screen.getByText("out").click()); // evicts room 1 → falls back to 3
    expect(screen.getByTestId("room")).toHaveTextContent("3");
    expect(listRooms().map((r) => r.roomId)).toEqual([3]);

    act(() => screen.getByText("out").click()); // last room gone → none
    expect(screen.getByTestId("room")).toHaveTextContent("none");
  });
});
