import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { BotMessage } from "../bot-message";

const att = {
  type: "random_pick",
  chosen: { id: 3, name: "An" },
  label: "trả tiền",
  candidates: [
    { id: 1, name: "Bình" },
    { id: 3, name: "An" },
  ],
};

describe("RandomPickCard via BotMessage", () => {
  it("shows the chosen person, the label, and the candidate pool", () => {
    render(<BotMessage body="🎲" attachments={att} roomId={3} />);
    expect(screen.getByText("An")).toBeInTheDocument();
    expect(screen.getByText("trả tiền")).toBeInTheDocument();
    expect(screen.getByText(/bốc trong 2 người/)).toBeInTheDocument();
    expect(screen.getByText(/Bình, An/)).toBeInTheDocument();
  });
});
