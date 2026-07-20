import { describe, expect, it } from "vitest";
import { mentionQuery } from "../mention-dropdown";

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
