import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AgentPanel } from "../agent-panel";
import * as api from "@/lib/api";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    roomAgent: vi.fn(),
    roomAgentVersions: vi.fn(),
    roomAgentVersion: vi.fn(),
    saveRoomAgent: vi.fn(),
    republishRoomAgent: vi.fn(),
  };
});

const VIEW = {
  agent: { slug: "phoenix", name: "Phoenix", persona: {} },
  profile: { id: 1, name: "default", business: "lunch", managed_by: "boot" },
  version: { id: 9, version: 3, actor: "member:4", note: "shorter replies", published_at: "2026-09-06T10:00:00Z" },
  editable: {
    prompt_body: "You are Phoenix.",
    prompt_append: ["Be brief."],
    skills: [{ name: "balances", description: "who owes who", body: "# balances" }],
    rules: [
      { slug: "money-safety", content: "tools own every number", tags: ["money"], editable: false },
      { slug: "house-style", content: "be warm", tags: [], editable: true },
    ],
  },
  readonly: {
    models: { text: "deepseek/v4" },
    caps: { max_tools: 40, max_seconds: 120 },
    builtin_tools: ["bash"],
    tool_packs: ["lunch_ledger"],
    pipeline_stages: ["model", "render", "run"],
  },
  source_etags: { "skill/balances": "e1", "rule/house-style": "e2" },
  can_edit: true,
  shared: false,
  scope: ["prompt.body", "prompt.append", "skills", "rules"],
};

const LOG = [
  { id: 9, version: 3, status: "published", actor: "member:4", note: "shorter replies",
    created_at: "2026-09-06T10:00:00Z", published_at: "2026-09-06T10:00:00Z", paths: ["prompt.append"] },
  { id: 5, version: 2, status: "superseded", actor: "member:7", note: "the regrettable one",
    created_at: "2026-09-05T10:00:00Z", published_at: "2026-09-05T10:00:00Z", paths: ["skills"] },
];

const view = (over: Partial<typeof VIEW> = {}) => ({ ...VIEW, ...over });

beforeEach(() => {
  vi.clearAllMocks();
  (api.roomAgent as any).mockResolvedValue(view());
  (api.roomAgentVersions as any).mockResolvedValue(LOG);
  (api.saveRoomAgent as any).mockResolvedValue({ version: 4 });
  (api.republishRoomAgent as any).mockResolvedValue({ version: 4, from_version: 2 });
});

const mount = (props: any = {}) =>
  render(<AgentPanel roomId={3} active version={0} onSaved={() => {}} {...props} />);

describe("AgentPanel", () => {
  it("shows what the room runs, and locks the money rule", async () => {
    mount();
    expect(await screen.findByDisplayValue("You are Phoenix.")).toBeEnabled();
    expect(screen.getByDisplayValue("be warm")).toBeEnabled();
    // the money rule is visible but not editable, and says why
    expect(screen.getByDisplayValue("tools own every number")).toBeDisabled();
    expect(screen.getByText(/Only an operator can change this one/)).toBeInTheDocument();
    // the parts a member may not touch are shown as facts, not fields
    expect(screen.getByText("deepseek/v4")).toBeInTheDocument();
    expect(screen.getByText("model → render → run")).toBeInTheDocument();
  });

  it("is read-only, with the reason, when the room is not bound", async () => {
    (api.roomAgent as any).mockResolvedValue(view({ can_edit: false, shared: true }));
    mount();
    expect(await screen.findByText(/shared default bot/)).toBeInTheDocument();
    expect(screen.getByDisplayValue("You are Phoenix.")).toBeDisabled();
    expect(screen.queryByRole("button", { name: /^publish/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /republish/i })).not.toBeInTheDocument();
  });

  it("warns when the bot is shared, and when it has stopped following deploys", async () => {
    (api.roomAgent as any).mockResolvedValue(view({ shared: true, profile: { ...VIEW.profile, managed_by: "human" } }));
    mount();
    expect(await screen.findByText(/every room that has not been given its own/)).toBeInTheDocument();
    expect(screen.getByText(/no longer picks up prompt or skill changes/)).toBeInTheDocument();
  });

  it("saves with the version and etags it loaded", async () => {
    mount();
    const body = await screen.findByDisplayValue("You are Phoenix.");
    fireEvent.change(body, { target: { value: "You are Phoenix. Be kind." } });
    fireEvent.change(screen.getByPlaceholderText(/What changed/), { target: { value: "kinder" } });
    fireEvent.click(screen.getByRole("button", { name: /^publish/i }));

    await waitFor(() => expect(api.saveRoomAgent).toHaveBeenCalled());
    const [roomId, payload] = (api.saveRoomAgent as any).mock.calls[0];
    expect(roomId).toBe(3);
    expect(payload.base_version_id).toBe(9);          // the version it read, for the 409 check
    expect(payload.source_etags).toEqual(VIEW.source_etags);
    expect(payload.prompt_body).toBe("You are Phoenix. Be kind.");
    expect(payload.note).toBe("kinder");
    // money rules go back as they came; tags are never sent
    expect(payload.rules).toEqual([
      { slug: "money-safety", content: "tools own every number" },
      { slug: "house-style", content: "be warm" },
    ]);
  });

  it("tells a member to reload rather than retrying when someone else got there first", async () => {
    const { ApiError } = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
    (api.saveRoomAgent as any).mockRejectedValue(new ApiError(409, "moved"));
    mount();
    fireEvent.change(await screen.findByDisplayValue("You are Phoenix."), { target: { value: "mine" } });
    fireEvent.click(screen.getByRole("button", { name: /^publish/i }));
    expect(await screen.findByText(/Someone else changed the bot/)).toBeInTheDocument();
  });

  it("offers Republish only on a superseded version, and asks first", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    mount();
    await screen.findByText(/the regrettable one/);
    const buttons = screen.getAllByRole("button", { name: /republish/i });
    expect(buttons).toHaveLength(1);                  // v3 is live, so only v2 offers it
    fireEvent.click(buttons[0]);
    await waitFor(() => expect(api.republishRoomAgent).toHaveBeenCalledWith(3, 2, "republished v2"));
    expect(confirm).toHaveBeenCalled();
  });

  it("opens a version's diff on demand", async () => {
    (api.roomAgentVersion as any).mockResolvedValue({ diff: "-old\n+new" });
    mount();
    fireEvent.click(await screen.findByText(/the regrettable one/));
    await waitFor(() => expect(api.roomAgentVersion).toHaveBeenCalledWith(3, 2));
    await waitFor(() =>
      expect(document.querySelector("pre")?.textContent).toBe("-old\n+new"));
  });

  it("does not fetch at all until its tab is open", () => {
    mount({ active: false });
    expect(api.roomAgent).not.toHaveBeenCalled();
  });
});
