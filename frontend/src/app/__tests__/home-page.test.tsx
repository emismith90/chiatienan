import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import Home, { SPLASH_MIN_MS } from "../page";

let session = { token: "tok" as string | null, roomId: 1 as number | null, ready: true };
vi.mock("@/lib/session", () => ({ useSession: () => session }));
vi.mock("@/components/chat/room-view", () => ({
  RoomView: ({ roomId }: { roomId: number }) => <p>room {roomId}</p>,
}));

beforeEach(() => {
  vi.useFakeTimers();
  session = { token: "tok", roomId: 1, ready: true };
});
afterEach(() => vi.useRealTimers());

describe("Home splash", () => {
  it("mounts the room under the splash, then lifts the splash after the hold", () => {
    render(<Home />);
    // Loading starts while the splash is still up…
    expect(screen.getByRole("status", { name: "Loading chiatienan" })).toBeInTheDocument();
    expect(screen.getByText("room 1").closest("[inert]")).not.toBeNull();

    act(() => { vi.advanceTimersByTime(SPLASH_MIN_MS); });

    // …and the view is live once it lifts.
    expect(screen.queryByRole("status", { name: "Loading chiatienan" })).toBeNull();
    expect(screen.getByText("room 1").closest("[inert]")).toBeNull();
  });

  it("keeps the splash up past the hold until the session is ready", () => {
    session = { token: null, roomId: null, ready: false };
    const { rerender } = render(<Home />);
    act(() => { vi.advanceTimersByTime(SPLASH_MIN_MS); });
    expect(screen.getByRole("status", { name: "Loading chiatienan" })).toBeInTheDocument();

    session = { token: null, roomId: null, ready: true };
    rerender(<Home />);
    expect(screen.queryByRole("status", { name: "Loading chiatienan" })).toBeNull();
    expect(screen.getByText("Join a room")).toBeInTheDocument();
  });
});
