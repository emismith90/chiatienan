import { describe, expect, it } from "vitest";
import { mentionQuery, spliceMention } from "../mention-dropdown";

describe("mentionQuery", () => {
  it("detects an @ at the caret", () => {
    expect(mentionQuery("hello @ph", 9)).toBe("ph");
    expect(mentionQuery("hello @", 7)).toBe("");
    expect(mentionQuery("hello world", 11)).toBeNull();
    expect(mentionQuery("a@b.com", 7)).toBeNull(); // email, not a mention
    expect(mentionQuery("@ph", 3)).toBe("ph"); // mention at absolute start of string
    expect(mentionQuery("@", 1)).toBe(""); // bare @ at start
  });
});

describe("spliceMention", () => {
  it("swallows the dangling word remainder after the caret", () => {
    // "@pho|enix" — caret sits right after "pho" (mention span is [0, 4)),
    // accepting "phoenix" should drop the trailing "enix" rather than leave it.
    const text = "@phoenix";
    const { next, caret } = spliceMention(text, 0, 4, "phoenix");
    expect(next).toBe("@phoenix ");
    expect(caret).toBe(next.length);
  });

  it("keeps following text when it is not a dangling remainder", () => {
    const text = "@pho hi";
    const { next } = spliceMention(text, 0, 4, "phoenix");
    expect(next).toBe("@phoenix hi");
  });

  it("does not add an extra space when already followed by whitespace", () => {
    const text = "@pho ";
    const { next } = spliceMention(text, 0, 4, "phoenix");
    expect(next).toBe("@phoenix ");
  });
});
