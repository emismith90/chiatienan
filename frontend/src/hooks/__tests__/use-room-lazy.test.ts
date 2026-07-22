import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

// useRoom pulls the member id + signOut from the session context.
vi.mock("@/lib/session", () => ({
  useSession: () => ({ signOut: vi.fn(), memberId: 1 }),
}));

// Stub the network. streamRoom never resolves so the mount loop parks after
// the initial windowed load; getMessages is driven per-test.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getMessages: vi.fn(),
    streamRoom: vi.fn(() => new Promise<void>(() => {})),
    postMessage: vi.fn(),
  };
});

import * as api from "@/lib/api";
import { useRoom } from "../use-room";

describe("useRoom lazy scrollback", () => {
  beforeEach(() => {
    (api.getMessages as any).mockReset();
  });

  it("prepends older messages using the oldest real id as the cursor", async () => {
    (api.getMessages as any).mockImplementation((_roomId: number, opts: any = {}) => {
      if (opts.beforeId != null) {
        // Cursor must be the oldest *loaded* id, not 0 or a pending bubble.
        expect(opts.beforeId).toBe(5);
        return Promise.resolve({
          messages: [
            { id: 3, kind: "text", body: "c" },
            { id: 4, kind: "text", body: "d" },
          ],
          has_more: false,
        });
      }
      // Initial mount asks for a recent day-window.
      expect(opts.days).toBeGreaterThan(0);
      return Promise.resolve({
        messages: [
          { id: 5, kind: "text", body: "e" },
          { id: 6, kind: "text", body: "f" },
        ],
        has_more: true,
      });
    });

    const { result } = renderHook(() => useRoom(1));

    await waitFor(() => expect(result.current.messages.map((m: any) => m.id)).toEqual([5, 6]));
    expect(result.current.hasMore).toBe(true);

    await act(async () => {
      await result.current.loadEarlier();
    });

    // Older page prepended, id-ascending order preserved; has_more flips off.
    expect(result.current.messages.map((m: any) => m.id)).toEqual([3, 4, 5, 6]);
    expect(result.current.hasMore).toBe(false);
  });

  it("no-ops loadEarlier when there is no more history", async () => {
    (api.getMessages as any).mockResolvedValue({
      messages: [{ id: 9, kind: "text", body: "x" }],
      has_more: false,
    });

    const { result } = renderHook(() => useRoom(2));
    await waitFor(() => expect(result.current.messages.map((m: any) => m.id)).toEqual([9]));

    await act(async () => {
      await result.current.loadEarlier();
    });

    const pageCalls = (api.getMessages as any).mock.calls.filter(
      (c: any[]) => c[1]?.beforeId != null,
    );
    expect(pageCalls.length).toBe(0); // never fetched an older page
  });
});
