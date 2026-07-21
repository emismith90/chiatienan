import { describe, it, expect, vi } from "vitest";
import { MemoryOutboxStore, flushOutbox, newRecord } from "../outbox";

async function seed(store: MemoryOutboxStore, roomId: number, bodies: string[]) {
  // Stagger createdAt so order is deterministic.
  await Promise.all(bodies.map((b, i) => store.add(newRecord(roomId, b, undefined, 1000 + i))));
}

describe("outbox", () => {
  it("lists a room's records in send (createdAt) order and scopes by room", async () => {
    const store = new MemoryOutboxStore();
    await store.add(newRecord(1, "b", undefined, 200));
    await store.add(newRecord(1, "a", undefined, 100));
    await store.add(newRecord(2, "other", undefined, 150));
    const recs = await store.list(1);
    expect(recs.map((r) => r.body)).toEqual(["a", "b"]);
  });

  it("flushes all records in order and empties the queue on success", async () => {
    const store = new MemoryOutboxStore();
    await seed(store, 1, ["one", "two", "three"]);
    const posted: string[] = [];
    const res = await flushOutbox(store, 1, async (r) => {
      posted.push(r.body);
    });
    expect(posted).toEqual(["one", "two", "three"]);
    expect(res.stoppedByError).toBe(false);
    expect(res.sent).toHaveLength(3);
    expect(await store.list(1)).toHaveLength(0);
  });

  it("stops at the first failure and leaves that record + later ones queued", async () => {
    const store = new MemoryOutboxStore();
    await seed(store, 1, ["one", "two", "three"]);
    const post = vi
      .fn<(r: any) => Promise<void>>()
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue(undefined);
    const res = await flushOutbox(store, 1, post);
    expect(res.stoppedByError).toBe(true);
    expect(res.sent).toHaveLength(1);
    // "one" removed; "two" (failed) and "three" (not attempted) remain, in order.
    const left = await store.list(1);
    expect(left.map((r) => r.body)).toEqual(["two", "three"]);
    // post was called for "one" and "two" only — the drain didn't reach "three".
    expect(post).toHaveBeenCalledTimes(2);
  });

  it("newRecord attaches images only when present and stamps the id", () => {
    const withImgs = newRecord(1, "", [{ data: "x", mimeType: "image/jpeg" }], 5);
    expect(withImgs.images).toHaveLength(1);
    expect(withImgs.id).toContain("o-5-");
    const noImgs = newRecord(1, "hi", [], 5);
    expect(noImgs.images).toBeUndefined();
  });
});
