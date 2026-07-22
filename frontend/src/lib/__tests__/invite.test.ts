import { describe, expect, it } from "vitest";
import { parseInviteToken } from "../invite";

describe("parseInviteToken", () => {
  it("extracts the token from a full invite link", () => {
    expect(parseInviteToken("https://chiatienan.duckdns.org/join/tB9b2QzH6ckjcGEjK8sgbQ")).toBe(
      "tB9b2QzH6ckjcGEjK8sgbQ",
    );
  });

  it("ignores query and hash after the token", () => {
    expect(parseInviteToken("https://x.org/join/abc-123?ref=sms#top")).toBe("abc-123");
  });

  it("handles a partial link without scheme", () => {
    expect(parseInviteToken("chiatienan.duckdns.org/join/abc_123")).toBe("abc_123");
    expect(parseInviteToken("/join/abc123")).toBe("abc123");
  });

  it("accepts a bare token", () => {
    expect(parseInviteToken("  abc-123_XYZ  ")).toBe("abc-123_XYZ");
  });

  it("rejects empty or junk input", () => {
    expect(parseInviteToken("")).toBeNull();
    expect(parseInviteToken("   ")).toBeNull();
    expect(parseInviteToken("not a token")).toBeNull();
    expect(parseInviteToken("https://example.com/rooms/5")).toBeNull();
  });
});
