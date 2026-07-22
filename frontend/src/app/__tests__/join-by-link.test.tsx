import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import JoinByLink from "../join/page";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

beforeEach(() => vi.clearAllMocks());

describe("Join-by-link page", () => {
  it("navigates to the token join flow for a pasted full link", () => {
    render(<JoinByLink />);
    fireEvent.change(screen.getByLabelText("Invite link"), {
      target: { value: "https://chiatienan.duckdns.org/join/abc123" },
    });
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));
    expect(push).toHaveBeenCalledWith("/join/abc123");
  });

  it("accepts a bare code and submits on Enter", () => {
    render(<JoinByLink />);
    fireEvent.change(screen.getByLabelText("Invite link"), { target: { value: "code-xyz" } });
    fireEvent.keyDown(screen.getByLabelText("Invite link"), { key: "Enter" });
    expect(push).toHaveBeenCalledWith("/join/code-xyz");
  });

  it("shows an error and does not navigate on junk input", () => {
    render(<JoinByLink />);
    fireEvent.change(screen.getByLabelText("Invite link"), { target: { value: "not a link" } });
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));
    expect(push).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/valid invite link/i);
  });
});
