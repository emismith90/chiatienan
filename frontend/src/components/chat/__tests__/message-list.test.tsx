import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MessageList } from "../message-list";

describe("MessageList context_reset divider", () => {
  it("renders a context_reset message as a centered divider showing its body", () => {
    const messages = [
      { id: 1, kind: "text", body: "hello", author: { id: 5, name: "An" } },
      { id: 2, kind: "context_reset", body: "🧹 Đã xoá ngữ cảnh" },
    ];
    render(<MessageList messages={messages as any} members={[]} roomId={1} />);
    expect(screen.getByText("🧹 Đã xoá ngữ cảnh")).toBeInTheDocument();
    // the human message still renders
    expect(screen.getByText("hello")).toBeInTheDocument();
  });
});
