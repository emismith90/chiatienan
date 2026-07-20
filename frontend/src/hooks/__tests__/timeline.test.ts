import { describe, expect, it } from "vitest";
import { mergeEvent, type RoomState } from "../use-room";

const empty: RoomState = { messages: [], typing: false, timelines: {} };

describe("mergeEvent agent timeline", () => {
  it("opens a timeline on run.started", () => {
    const s = mergeEvent(empty, { type: "agent.run.started", turn_id: "t1" });
    expect(s.timelines["t1"]).toEqual([]);
  });
  it("appends tool steps", () => {
    let s = mergeEvent(empty, { type: "agent.run.started", turn_id: "t1" });
    s = mergeEvent(s, { type: "agent.tool.start", turn_id: "t1", call_id: "c1", name: "propose_meal", args: {} });
    s = mergeEvent(s, { type: "agent.tool.result", turn_id: "t1", call_id: "c1", name: "propose_meal", status: "completed", result: {} });
    expect(s.timelines["t1"].filter((x) => x.kind === "tool").length).toBe(1);
    expect(s.timelines["t1"][0].status).toBe("completed");
  });
  it("marks finished", () => {
    let s = mergeEvent(empty, { type: "agent.run.started", turn_id: "t1" });
    s = mergeEvent(s, { type: "agent.run.finished", turn_id: "t1" });
    expect(s.timelines["t1"]).toBeDefined();
  });
});
