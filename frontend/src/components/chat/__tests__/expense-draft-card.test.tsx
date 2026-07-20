import { describe, expect, it } from "vitest";
import { perHead } from "../expense-draft-card";

describe("perHead", () => {
  it("splits the total evenly across billed members and guests", () => {
    expect(perHead(400_000, 3, 1)).toBe(100_000);
  });

  it("floors the result when the split is not even", () => {
    expect(perHead(100, 3, 0)).toBe(33);
  });

  it("returns 0 when there are no heads to bill (avoids divide-by-zero)", () => {
    expect(perHead(400_000, 0, 0)).toBe(0);
  });

  it("counts guests toward the head count", () => {
    expect(perHead(300_000, 2, 1)).toBe(perHead(300_000, 3, 0));
  });
});
