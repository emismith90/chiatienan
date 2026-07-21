import { describe, it, expect } from "vitest";
import { fitWithin, MAX_EDGE } from "../image";

describe("fitWithin", () => {
  it("leaves images at or below the max edge unchanged", () => {
    expect(fitWithin(800, 600, 1600)).toEqual({ width: 800, height: 600 });
    expect(fitWithin(1600, 1200, 1600)).toEqual({ width: 1600, height: 1200 });
  });

  it("scales a landscape photo so the width becomes the max edge", () => {
    expect(fitWithin(3200, 2400, 1600)).toEqual({ width: 1600, height: 1200 });
  });

  it("scales a portrait photo so the height becomes the max edge", () => {
    expect(fitWithin(2400, 3200, 1600)).toEqual({ width: 1200, height: 1600 });
  });

  it("preserves aspect ratio and never returns a zero dimension", () => {
    const { width, height } = fitWithin(4000, 100, 1600);
    expect(width).toBe(1600);
    expect(height).toBe(Math.max(1, Math.round(100 * (1600 / 4000))));
    expect(height).toBeGreaterThanOrEqual(1);
  });

  it("defaults to MAX_EDGE and handles degenerate sizes", () => {
    expect(fitWithin(0, 0)).toEqual({ width: 0, height: 0 });
    expect(fitWithin(MAX_EDGE * 2, MAX_EDGE * 2).width).toBe(MAX_EDGE);
  });
});
