import { describe, expect, it } from "vitest";
import { mentionQuery, spliceMention } from "../mention-dropdown";

describe("mentionQuery", () => {
  it("detects an @ at the caret", () => {
    expect(mentionQuery("hello @bo", 9)).toBe("bo");
    expect(mentionQuery("hello @", 7)).toBe("");
    expect(mentionQuery("hello world", 11)).toBeNull();
    expect(mentionQuery("a@b.com", 7)).toBeNull(); // email, not a mention
    expect(mentionQuery("@bo", 3)).toBe("bo"); // mention at absolute start of string
    expect(mentionQuery("@", 1)).toBe(""); // bare @ at start
  });
});

describe("spliceMention", () => {
  it("swallows the dangling word remainder after the caret", () => {
    // "@bo|ot" — caret sits right after "bo" (mention span is [0, 3)),
    // accepting "bot" should drop the trailing "ot" instead of leaving "bot ot".
    const text = "@boot";
    const { next, caret } = spliceMention(text, 0, 3, "bot");
    expect(next).toBe("@bot ");
    expect(caret).toBe(next.length);
  });

  it("keeps following text when it is not a dangling remainder", () => {
    const text = "@bo hi";
    const { next } = spliceMention(text, 0, 3, "bot");
    expect(next).toBe("@bot hi");
  });

  it("does not add an extra space when already followed by whitespace", () => {
    const text = "@bo ";
    const { next } = spliceMention(text, 0, 3, "bot");
    expect(next).toBe("@bot ");
  });
});
