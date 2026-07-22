import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

// useRoom pulls the member id + signOut from the session context.
vi.mock("@/lib/session", () => ({
  useSession: () => ({ signOut: vi.fn(), memberId: 1 }),
}));

// Keep the real ApiError (send/postRecord branch on `instanceof ApiError`),
// stub the network calls. streamRoom never resolves so the mount loop parks.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getMessages: vi.fn().mockResolvedValue({ messages: [] }),
    streamRoom: vi.fn(() => new Promise<void>(() => {})),
    postMessage: vi.fn(),
  };
});

import * as api from "@/lib/api";
import { useRoom } from "../use-room";

beforeEach(() => {
  vi.useFakeTimers();
  (api.postMessage as any).mockReset();
});
afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
});

describe("useRoom outbox retry", () => {
  it("retries a network-failed send without waiting for an online event", async () => {
    // A send whose fetch rejects at the network level (NOT an ApiError) while
    // navigator.onLine stays true — the network-switch case from the bug report.
    // First attempt fails, the next succeeds.
    (api.postMessage as any)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue(undefined);

    const { result } = renderHook(() => useRoom(10));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0); // let mount effects settle
    });

    await act(async () => {
      await result.current.send("hi");
    });
    // Failed → queued in the outbox (one attempt so far).
    expect(api.postMessage).toHaveBeenCalledTimes(1);

    // No "online" event is ever dispatched. The queued record must still be
    // retried on its own; without the retry it stays stuck forever.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(api.postMessage).toHaveBeenCalledTimes(2);

    // Drained: the retry loop stops once the outbox is empty (no hot loop).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(api.postMessage).toHaveBeenCalledTimes(2);
  });
});
