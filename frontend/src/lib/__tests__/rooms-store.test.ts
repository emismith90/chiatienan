import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  activeRoom, getProfile, listRooms, migrateLegacy, removeRoom,
  renameRoom, saveProfile, touchRoom, upsertRoom,
} from "../rooms-store";

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-07-22T12:00:00Z"));
});
afterEach(() => vi.useRealTimers());

const tick = () => vi.advanceTimersByTime(1000);

describe("rooms list", () => {
  it("starts empty", () => {
    expect(listRooms()).toEqual([]);
    expect(activeRoom()).toBeNull();
  });

  it("upsert adds a room and makes it active; newest access wins", () => {
    upsertRoom({ roomId: 1, roomName: "A", token: "ta" });
    tick();
    upsertRoom({ roomId: 2, roomName: "B", token: "tb" });
    expect(activeRoom()?.roomId).toBe(2);
    tick();
    touchRoom(1);
    expect(activeRoom()?.roomId).toBe(1);
    expect(listRooms().map((r) => r.roomId)).toEqual([1, 2]);
  });

  it("upsert replaces an existing entry (rejoin refreshes the token)", () => {
    upsertRoom({ roomId: 1, roomName: "A", token: "old" });
    tick();
    upsertRoom({ roomId: 1, roomName: "A2", token: "new" });
    expect(listRooms()).toHaveLength(1);
    expect(activeRoom()).toMatchObject({ token: "new", roomName: "A2" });
  });

  it("renameRoom sets the name without bumping last access", () => {
    upsertRoom({ roomId: 1, roomName: "", token: "ta" });
    tick();
    upsertRoom({ roomId: 2, roomName: "B", token: "tb" });
    tick();
    renameRoom(1, "Named");
    expect(activeRoom()?.roomId).toBe(2); // rename must not steal the pointer
    expect(listRooms().find((r) => r.roomId === 1)?.roomName).toBe("Named");
  });

  it("removeRoom evicts; the next most-recent becomes active", () => {
    upsertRoom({ roomId: 1, roomName: "A", token: "ta" });
    tick();
    upsertRoom({ roomId: 2, roomName: "B", token: "tb" });
    removeRoom(2);
    expect(activeRoom()?.roomId).toBe(1);
    removeRoom(1);
    expect(activeRoom()).toBeNull();
  });

  it("survives corrupt storage", () => {
    localStorage.setItem("chiatienan.rooms", "not json");
    expect(listRooms()).toEqual([]);
    localStorage.setItem("chiatienan.rooms", JSON.stringify([{ bogus: true }]));
    expect(listRooms()).toEqual([]);
  });
});

describe("legacy migration", () => {
  it("moves the single token/room pair into the list and removes legacy keys", () => {
    localStorage.setItem("chiatienan.token", "legacy-tok");
    localStorage.setItem("chiatienan.room_id", "7");
    migrateLegacy();
    expect(activeRoom()).toMatchObject({ roomId: 7, token: "legacy-tok", roomName: "" });
    expect(localStorage.getItem("chiatienan.token")).toBeNull();
    expect(localStorage.getItem("chiatienan.room_id")).toBeNull();
  });

  it("is a no-op without legacy keys and never duplicates an existing entry", () => {
    upsertRoom({ roomId: 7, roomName: "A", token: "current" });
    localStorage.setItem("chiatienan.token", "stale");
    localStorage.setItem("chiatienan.room_id", "7");
    migrateLegacy();
    expect(listRooms()).toHaveLength(1);
    expect(activeRoom()?.token).toBe("current");
    migrateLegacy(); // idempotent
    expect(listRooms()).toHaveLength(1);
  });
});

describe("saved profile", () => {
  it("round-trips and merges defined keys only", () => {
    expect(getProfile()).toEqual({});
    saveProfile({ nickname: "an", pin: "1234", display_name: "An" });
    saveProfile({ bank_code: "VCB", display_name: undefined });
    expect(getProfile()).toEqual({
      nickname: "an", pin: "1234", display_name: "An", bank_code: "VCB",
    });
  });

  it("survives corrupt storage", () => {
    localStorage.setItem("chiatienan.profile", "{{{");
    expect(getProfile()).toEqual({});
  });
});
