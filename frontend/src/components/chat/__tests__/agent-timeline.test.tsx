import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { AgentTimeline } from "../agent-timeline";
import type { TimelineStep } from "@/hooks/use-room";

const steps: TimelineStep[] = [
  { kind: "tool", name: "propose_meal", status: "completed" },
  { kind: "text", text: "done" },
];

describe("AgentTimeline", () => {
  it("shows the expanded step list when live", () => {
    render(<AgentTimeline steps={steps} live={true} />);
    expect(screen.getByText("Bot đang xử lý…")).toBeInTheDocument();
    expect(screen.getByText("done")).toBeInTheDocument();
  });

  it("collapses to the summary line when not live", () => {
    render(<AgentTimeline steps={steps} live={false} />);
    expect(screen.getByText("▸ 2 bước")).toBeInTheDocument();
    expect(screen.queryByText("done")).not.toBeInTheDocument();
  });
});
