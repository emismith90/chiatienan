import { describe, it, expect, vi, beforeEach } from "vitest";
import * as api from "../api";

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

it("attaches bearer token and posts a message", async () => {
  api.setToken("t123");
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ ok: true, id: 9 }), { status: 200 })
  );
  vi.stubGlobal("fetch", fetchMock);
  const res = await api.postMessage(1, "hi");
  expect(res.id).toBe(9);
  const [, init] = fetchMock.mock.calls[0];
  expect((init.headers as any).Authorization).toBe("Bearer t123");
});

describe("session storage helpers", () => {
  it("round-trips token and room id, and clears both", () => {
    expect(api.getToken()).toBeNull();
    expect(api.getRoomId()).toBeNull();

    api.setToken("abc");
    api.setRoomId(42);
    expect(api.getToken()).toBe("abc");
    expect(api.getRoomId()).toBe(42);

    api.clearSession();
    expect(api.getToken()).toBeNull();
    expect(api.getRoomId()).toBeNull();
  });
});

describe("ApiError", () => {
  it("throws ApiError with status and detail on non-2xx response", async () => {
    api.setToken("t123");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "sai biệt danh hoặc PIN" }), { status: 401 })
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.getMe()).rejects.toMatchObject({
      status: 401,
      message: "sai biệt danh hoặc PIN",
    });
  });
});

describe("requests without a token", () => {
  it("does not attach an Authorization header when no token is set", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ room_id: 1, name: "Room" }), { status: 200 })
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.roomInfo("invite-tok");
    const [, init] = fetchMock.mock.calls[0];
    expect((init.headers as any).Authorization).toBeUndefined();
  });
});

describe("getMembers", () => {
  it("fetches room members", async () => {
    api.setToken("t123");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([{ id: 1, display_name: "A", nickname: "a" }]), { status: 200 })
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await api.getMembers(1);
    expect(res).toEqual([{ id: 1, display_name: "A", nickname: "a" }]);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/rooms/1/members");
  });
});

describe("streamRoom", () => {
  it("throws ApiError (not fetch) when no token is set", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const controller = new AbortController();
    await expect(
      api.streamRoom(1, 0, () => {}, controller.signal)
    ).rejects.toBeInstanceOf(api.ApiError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("throws ApiError when the response is not ok (so the caller can reconnect)", async () => {
    api.setToken("t123");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("nope", { status: 500 })
    );
    vi.stubGlobal("fetch", fetchMock);

    const controller = new AbortController();
    await expect(
      api.streamRoom(1, 0, () => {}, controller.signal)
    ).rejects.toBeInstanceOf(api.ApiError);
  });

  it("parses SSE events from the stream body via parseSSE and calls onEvent", async () => {
    api.setToken("t123");
    const chunks = [
      'data: {"type":"message","id":1}\n\n',
      'data: {"type":"message","id":2}\n\n',
    ];
    let i = 0;
    const stream = new ReadableStream({
      pull(controller) {
        if (i < chunks.length) {
          controller.enqueue(new TextEncoder().encode(chunks[i++]));
        } else {
          controller.close();
        }
      },
    });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(stream, { status: 200 })
    );
    vi.stubGlobal("fetch", fetchMock);

    const events: any[] = [];
    const controller = new AbortController();
    await api.streamRoom(1, 0, (e) => events.push(e), controller.signal);

    expect(events).toEqual([
      { type: "message", id: 1 },
      { type: "message", id: 2 },
    ]);
  });
});
