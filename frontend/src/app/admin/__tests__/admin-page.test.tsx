import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import AdminPage from "../page";
import * as admin from "@/lib/admin-api";

vi.mock("@/lib/admin-api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/admin-api")>("@/lib/admin-api");
  return {
    ...actual,
    signIn: vi.fn(),
    loadCred: vi.fn(),
    clearCred: vi.fn(),
    businesses: vi.fn(),
    profiles: vi.fn(),
    agents: vi.fn(),
    bindings: vi.fn(),
    bind: vi.fn(),
    unbind: vi.fn(),
    patchAgent: vi.fn(),
    sources: vi.fn(),
    versions: vi.fn(),
    version: vi.fn(),
    versionDiff: vi.fn(),
    publish: vi.fn(),
    proposals: vi.fn(),
    approveProposal: vi.fn(),
    audit: vi.fn(),
  };
});

const m = admin as unknown as Record<string, ReturnType<typeof vi.fn>>;

const BUSINESS = { id: 1, slug: "lunch", name: "Lunch", tool_packs: ["lunch_ledger"] };
const PROFILE = { id: 1, business_id: 1, name: "default", managed_by: "boot", published_version_id: 9 };
const PHOENIX = {
  id: 1, business_id: 1, slug: "phoenix", name: "Phoenix", role: "manager", is_default: true,
  profile_id: 1, delegates_to: [], capabilities: {}, description: null,
};
const STEWARD = {
  id: 3, business_id: 1, slug: "steward", name: "Steward", role: "sub", is_default: false,
  profile_id: 3, delegates_to: [], capabilities: { cms: ["read", "draft"] }, description: "reviews",
};

function signedIn() {
  m.loadCred.mockReturnValue({ password: "pw", actor: "hung" });
  m.businesses.mockResolvedValue([BUSINESS]);
  m.profiles.mockResolvedValue([PROFILE]);
  m.agents.mockResolvedValue([PHOENIX, STEWARD]);
  m.bindings.mockResolvedValue([]);
  m.sources.mockResolvedValue([]);
  m.versions.mockResolvedValue([]);
  m.proposals.mockResolvedValue([]);
  m.audit.mockResolvedValue([]);
}

beforeEach(() => {
  vi.clearAllMocks();
  m.loadCred.mockReturnValue(null);
});

it("asks for the password first, and does not load anything until it has one", async () => {
  render(<AdminPage />);
  expect(await screen.findByLabelText("admin password")).toBeInTheDocument();
  expect(m.businesses).not.toHaveBeenCalled();
});

it("keeps you on the form when the password is refused", async () => {
  const { ApiError } = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  m.signIn.mockRejectedValue(new ApiError(401, "unauthorized"));
  render(<AdminPage />);

  fireEvent.change(await screen.findByLabelText("admin password"), { target: { value: "nope" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

  expect(await screen.findByText(/was not accepted/i)).toBeInTheDocument();
  expect(screen.getByLabelText("admin password")).toBeInTheDocument();
});

it("shows what is live, and flags a profile a deploy still refreshes", async () => {
  signedIn();
  render(<AdminPage />);

  expect(await screen.findByRole("button", { name: "Content" })).toBeInTheDocument();
  expect(screen.getByText("boot")).toBeInTheDocument();
  expect(await screen.findByText(/No space is bound/i)).toBeInTheDocument();
});

it("binds a space only after saying what a binding lets people do", async () => {
  signedIn();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<AdminPage />);

  fireEvent.change(await screen.findByLabelText("space id"), { target: { value: "3" } });
  fireEvent.change(screen.getByLabelText("agent"), { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: "Bind" }));

  await waitFor(() => expect(m.bind).toHaveBeenCalledWith("3", 1));
  expect(confirm.mock.calls[0][0]).toMatch(/able to edit/i);
});

it("warns that delegation changes the tool manifest before wiring a sub-agent", async () => {
  signedIn();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<AdminPage />);

  fireEvent.click(await screen.findByLabelText("ask_steward"));

  await waitFor(() => expect(m.patchAgent).toHaveBeenCalledWith(1, { delegates_to: [3] }));
  expect(confirm.mock.calls[0][0]).toMatch(/benchmark/i);
});

it("shows a version's changed paths and its diff", async () => {
  signedIn();
  m.versions.mockResolvedValue([
    { id: 9, version: 2, status: "published", actor: "hung", note: "shorter", created_at: "2026-09-06T10:00:00Z", published_at: "2026-09-06T10:00:00Z", spec: null },
  ]);
  m.versionDiff.mockResolvedValue({ version: 2, against: 1, paths: ["prompt.body"], diff: "-old\n+new" });
  m.version.mockResolvedValue({ id: 9, version: 2, status: "published", actor: "hung", note: null, created_at: "", published_at: null, spec: { prompt: { body: "new" } } });
  render(<AdminPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Content" }));
  fireEvent.click(await screen.findByRole("button", { name: /^v2/ }));

  expect(await screen.findByText(/Changed against v1: prompt.body/)).toBeInTheDocument();
  expect(document.querySelector("pre")?.textContent).toContain("+new");
});

it("names every gate that refused a publish", async () => {
  signedIn();
  m.versions.mockResolvedValue([
    { id: 10, version: 3, status: "draft", actor: "hung", note: null, created_at: "2026-09-07T10:00:00Z", published_at: null, spec: null },
  ]);
  m.versionDiff.mockResolvedValue({ version: 3, against: 2, paths: ["skills"], diff: "" });
  m.version.mockResolvedValue({ id: 10, version: 3, status: "draft", actor: "hung", note: null, created_at: "", published_at: null, spec: { prompt: { body: "x" } } });
  m.publish.mockRejectedValue(new admin.GateFailure([{ gate: "eval", message: "no suite ran" }]));
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<AdminPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Content" }));
  fireEvent.click(await screen.findByRole("button", { name: /^v3/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Publish v3" }));

  expect(await screen.findByText(/eval: no suite ran/)).toBeInTheDocument();
});

it("approves a proposal, and shows why the last attempt failed", async () => {
  signedIn();
  m.proposals.mockResolvedValue([
    {
      id: 7, business_id: 1, agent_id: 3, profile_id: 1, version_id: 11,
      rationale: "twelve unanswered 'why this number'", diff: { paths: ["skills"], unified: "+explain" },
      source_changes: [{ kind: "skill", slug: "balances" }], status: "pending",
      decided_by: null, decided_at: null, last_error: "eval: no suite ran", created_at: "2026-09-07T10:00:00Z",
    },
  ]);
  render(<AdminPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Proposals" }));
  fireEvent.click(await screen.findByRole("button", { name: /#7/ }));
  expect(await screen.findByText(/eval: no suite ran/)).toBeInTheDocument();
  expect(screen.getByText(/skill\/balances/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Approve and publish" }));
  await waitFor(() => expect(m.approveProposal).toHaveBeenCalledWith(7));
});

it("sends you back to the form when the password has stopped working", async () => {
  const { ApiError } = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  m.loadCred.mockReturnValue({ password: "stale", actor: "hung" });
  m.businesses.mockRejectedValue(new ApiError(401, "unauthorized"));
  m.profiles.mockResolvedValue([]);
  m.agents.mockResolvedValue([]);
  m.bindings.mockResolvedValue([]);
  render(<AdminPage />);

  expect(await screen.findByLabelText("admin password")).toBeInTheDocument();
  expect(m.clearCred).toHaveBeenCalled();
});
