/** The credential split (plan Phase 12.1).
 *
 * The point of a second client is that two credentials never cross: the room bearer must
 * not reach `/api/admin/*`, and `ADMIN_PASSWORD` must not reach a room route. Both
 * directions are asserted here because a single shared `req()` would have made either
 * mistake invisible.
 */
import { beforeEach, expect, it, vi } from "vitest";
import * as admin from "../admin-api";
import * as api from "../api";
import { upsertRoom } from "../rooms-store";

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  admin.clearCred();
  vi.restoreAllMocks();
});

const ok = (body: unknown, init?: ResponseInit) =>
  vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 200, ...init }));

it("sends the admin password and the actor, and never the room bearer", async () => {
  upsertRoom({ roomId: 1, roomName: "R", token: "room-token" });
  admin.setCred({ password: "pw", actor: "hung" });
  const fetchMock = ok([{ id: 1, slug: "lunch" }]);
  vi.stubGlobal("fetch", fetchMock);

  await admin.businesses();

  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/admin/businesses");
  expect((init.headers as any)["X-Admin-Password"]).toBe("pw");
  expect((init.headers as any)["X-Actor"]).toBe("hung");
  expect((init.headers as any).Authorization).toBeUndefined();
});

it("never sends the admin password to a room route", async () => {
  upsertRoom({ roomId: 1, roomName: "R", token: "room-token" });
  admin.setCred({ password: "pw", actor: "hung" });
  const fetchMock = ok({ ok: true });
  vi.stubGlobal("fetch", fetchMock);

  await api.getMe();

  const [, init] = fetchMock.mock.calls[0];
  expect((init.headers as any).Authorization).toBe("Bearer room-token");
  expect((init.headers as any)["X-Admin-Password"]).toBeUndefined();
});

it("refuses to call anything without a credential", async () => {
  await expect(admin.businesses()).rejects.toMatchObject({ status: 401 });
});

it("keeps the credential in sessionStorage, and forgets it on sign out", async () => {
  admin.setCred({ password: "pw", actor: "hung" });
  expect(sessionStorage.getItem("chiatienan.admin_pw")).toBe("pw");
  expect(localStorage.getItem("chiatienan.admin_pw")).toBeNull();
  admin.clearCred();
  expect(admin.loadCred()).toBeNull();
  expect(sessionStorage.getItem("chiatienan.admin_pw")).toBeNull();
});

it("does not keep a password the server rejected", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "unauthorized" }), { status: 401 })),
  );
  await expect(admin.signIn({ password: "wrong", actor: "hung" })).rejects.toMatchObject({ status: 401 });
  expect(admin.loadCred()).toBeNull();
});

it("turns a 422 gate refusal into a GateFailure that names each gate", async () => {
  admin.setCred({ password: "pw", actor: "hung" });
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ detail: { gates: [{ gate: "eval", message: "no suite ran" }, { gate: "money_safety", message: "bash" }] } }),
        { status: 422 },
      ),
    ),
  );
  const err = await admin.publish(1, 4, {}).catch((e) => e);
  expect(err).toBeInstanceOf(admin.GateFailure);
  expect(err.gates.map((g: any) => g.gate)).toEqual(["eval", "money_safety"]);
});

it("carries a source's ETag through, so the next save can send If-Match", async () => {
  admin.setCred({ password: "pw", actor: "hung" });
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ kind: "rule", slug: "house", body: "be warm" }), {
        status: 200,
        headers: { ETag: "abc123" },
      }),
    ),
  );
  const saved = await admin.putSource(1, "rule", "house", { body: "be warm" }, "old");
  expect(saved.etag).toBe("abc123");
});
