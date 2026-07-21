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

    // the divider body text is present
    const divider = screen.getByText("🧹 Đã xoá ngữ cảnh");
    expect(divider).toBeInTheDocument();

    // the divider is rendered in a centered container (not as a right-aligned human bubble)
    expect(divider.closest(".justify-center")).toBeInTheDocument();

    // the human message still renders
    const humanMessage = screen.getByText("hello");
    expect(humanMessage).toBeInTheDocument();

    // the human message is NOT in the centered divider container (it's right-aligned)
    expect(humanMessage.closest(".justify-center")).not.toBeInTheDocument();
  });
});
