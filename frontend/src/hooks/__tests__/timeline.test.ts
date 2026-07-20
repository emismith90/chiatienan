import { describe, expect, it } from "vitest";
import { mergeEvent, type RoomState } from "../use-room";

const empty: RoomState = { messages: [], typing: false, timelines: {}, activeTurn: null };

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

  it("run.started sets activeTurn to the new turn_id", () => {
    const s = mergeEvent(empty, { type: "agent.run.started", turn_id: "t1" });
    expect(s.activeTurn).toBe("t1");
  });

  it("run.finished for the active turn clears activeTurn", () => {
    let s = mergeEvent(empty, { type: "agent.run.started", turn_id: "t1" });
    s = mergeEvent(s, { type: "agent.run.finished", turn_id: "t1" });
    expect(s.activeTurn).toBeNull();
  });

  it("run.error for the active turn clears activeTurn", () => {
    let s = mergeEvent(empty, { type: "agent.run.started", turn_id: "t1" });
    s = mergeEvent(s, { type: "agent.run.error", turn_id: "t1" });
    expect(s.activeTurn).toBeNull();
  });

  it("run.finished for a non-active turn_id does not clobber a different active turn", () => {
    // t1 starts and finishes, then t2 starts (becomes active). A late/duplicate
    // run.finished for t1 must not clear the currently-active t2.
    let s = mergeEvent(empty, { type: "agent.run.started", turn_id: "t1" });
    s = mergeEvent(s, { type: "agent.run.finished", turn_id: "t1" });
    s = mergeEvent(s, { type: "agent.run.started", turn_id: "t2" });
    expect(s.activeTurn).toBe("t2");

    s = mergeEvent(s, { type: "agent.run.finished", turn_id: "t1" });
    expect(s.activeTurn).toBe("t2");
  });

  it("matches tool.result by call_id, not by name, when two tools share a name", () => {
    let s = mergeEvent(empty, { type: "agent.run.started", turn_id: "t1" });
    s = mergeEvent(s, { type: "agent.tool.start", turn_id: "t1", call_id: "c1", name: "propose_meal", args: {} });
    s = mergeEvent(s, { type: "agent.tool.start", turn_id: "t1", call_id: "c2", name: "propose_meal", args: {} });

    // Resolve the FIRST call by its call_id.
    s = mergeEvent(s, { type: "agent.tool.result", turn_id: "t1", call_id: "c1", name: "propose_meal", status: "completed", result: {} });

    const steps = s.timelines["t1"].filter((x) => x.kind === "tool");
    expect(steps.length).toBe(2);
    expect(steps[0].status).toBe("completed"); // first call_id resolved
    expect(steps[1].status).toBe("running"); // second call_id still running
  });
});
