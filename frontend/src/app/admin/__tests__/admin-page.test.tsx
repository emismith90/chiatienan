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
    deleteSource: vi.fn(),
    catalogue: vi.fn(),
    registry: vi.fn(),
    models: vi.fn(),
    resolved: vi.fn(),
    collections: vi.fn(),
    putCollection: vi.fn(),
    deleteCollection: vi.fn(),
    patchDraft: vi.fn(),
    createDraft: vi.fn(),
    putSource: vi.fn(),
    retire: vi.fn(),
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

const CATALOGUE = {
  packs: [
    {
      id: "lunch_ledger", version: "3", handles_money: true, evidence: true, dynamic: false,
      framework_managed: false, draft_kinds: ["expense_draft"], error: null,
      tool_names: ["propose_meal", "void_meal"],
      tools: [
        { name: "propose_meal", description: "Propose a meal", schema: { type: "object", properties: {} }, money: true, commit: true, cancel: false },
        { name: "void_meal", description: "Void a meal", schema: { type: "object", properties: {} }, money: true, commit: true, cancel: false },
      ],
    },
    {
      id: "delegation", version: "1", handles_money: false, evidence: true, dynamic: true,
      framework_managed: true, draft_kinds: [], tools: [], tool_names: [], error: null,
    },
  ],
  builtin_tools: ["read", "write", "bash"],
  risky_builtin_tools: ["bash", "write"],
  source_kinds: ["prompt", "rule", "skill", "template"],
};
const PLUGINS = [
  { id: "kernos.prompt.template", version: "1", stage: "prompt", config_schema: { type: "object" }, schema_hash: "ab", handles_money: false },
];
const MODELS = [
  { model_id: "m/one", provider: "p", name: "One", input: ["text"], context_window: 1, max_tokens: 1, probe: { ok: true, checked_at: "2026-09-01T00:00:00Z" } },
];
const RESOLVED = {
  space_id: "1",
  resolution: { bound: true, agent: { slug: "phoenix", business_id: 1 }, profile_id: 1, version_id: 9 },
  spec: {
    prompt: { body: "You are Phoenix", append: [] },
    rules: [{ slug: "money-safety", content: "no bash maths", tags: ["money"] }],
    skills: [{ name: "balances", description: "d", body: "SNAPSHOT BODY", delivery: "inline" }],
    tool_packs: [{ pack: "lunch_ledger", tools: { void_meal: { enabled: false } } }],
    builtin_tools: ["read"],
    models: { text: "m/one", vision: null, thinking: "medium" },
    caps: { max_tools: 40, max_seconds: 120 },
    templates: [],
  },
  pipeline: [{ stage: "prompt", plugin: "kernos.prompt.template", version: "1", config: {} }],
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
  m.catalogue.mockResolvedValue(CATALOGUE);
  m.registry.mockResolvedValue(PLUGINS);
  m.models.mockResolvedValue(MODELS);
  m.collections.mockResolvedValue([]);
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

// ------------------------------------------------------- Components (Phase 13.2)

it("lists a pack's tools with their schemas, which no route exposed before", async () => {
  signedIn();
  render(<AdminPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Components" }));

  expect(await screen.findByRole("button", { name: /propose_meal/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /propose_meal/ }));
  expect(document.querySelector("pre")?.textContent).toContain('"type": "object"');
});

it("badges a tool the profile turned off, for the space you look up", async () => {
  signedIn();
  m.resolved.mockResolvedValue(RESOLVED);
  render(<AdminPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Components" }));
  fireEvent.change(await screen.findByLabelText("space id"), { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: "Look up" }));

  const off = await screen.findByRole("button", { name: /void_meal/ });
  expect(off.textContent).toMatch(/off/);
  expect(screen.getByRole("button", { name: /propose_meal/ }).textContent).toMatch(/on/);
});

it("never claims a per-turn pack's tools are on or off", async () => {
  signedIn();
  m.resolved.mockResolvedValue(RESOLVED);
  render(<AdminPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Components" }));
  fireEvent.change(await screen.findByLabelText("space id"), { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: "Look up" }));

  expect(await screen.findByText(/must not list it/i)).toBeInTheDocument();
  expect(screen.getByText("delegation")).toBeInTheDocument();
});

it("traces the prompt back to its source rows, and says when one has moved on", async () => {
  signedIn();
  m.resolved.mockResolvedValue(RESOLVED);
  m.sources.mockResolvedValue([
    { id: 1, kind: "prompt", slug: "system", title: "system", body: "You are Phoenix", frontmatter: {}, etag: "e1", updated_by: "boot", updated_at: "2026-09-01T00:00:00Z" },
    { id: 2, kind: "skill", slug: "balances", title: "balances", body: "EDITED SINCE", frontmatter: {}, etag: "e2", updated_by: "hung", updated_at: "2026-09-08T00:00:00Z" },
  ]);
  render(<AdminPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Components" }));
  fireEvent.change(await screen.findByLabelText("space id"), { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: "Look up" }));

  expect(await screen.findByText(/prompt\/system · boot/)).toBeInTheDocument();
  // the skill's source was edited after the snapshot: say so rather than show today's body
  expect(screen.getByText(/source changed since this version/i)).toBeInTheDocument();
  // a money rule with no source row at all is not silently attributed to one
  expect(screen.getByText(/no source — spec only/)).toBeInTheDocument();
});

// -------------------------------------------------------- Assembly (Phase 13.3)

const DRAFT = {
  id: 10, version: 3, status: "draft", actor: "hung", note: null,
  created_at: "2026-09-07T10:00:00Z", published_at: null,
  spec: {
    prompt: { body: "x" },
    tool_packs: [{ pack: "lunch_ledger", tools: {} }],
    builtin_tools: ["read"],
    models: { text: "m/one", vision: null, thinking: "medium" },
    caps: { max_tools: 40, max_seconds: 120 },
    meta: { handles_money: true },
  },
};

function openTheDraft() {
  m.versions.mockResolvedValue([{ ...DRAFT, spec: null }]);
  m.versionDiff.mockResolvedValue({ version: 3, against: 2, paths: [], diff: "" });
  m.version.mockResolvedValue(DRAFT);
}

it("sends the whole tool_packs array when a pack is unticked, because a merge replaces lists", async () => {
  signedIn();
  openTheDraft();
  m.patchDraft.mockResolvedValue({ ...DRAFT, spec: { ...DRAFT.spec, tool_packs: [] } });
  render(<AdminPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Content" }));
  fireEvent.click(await screen.findByRole("button", { name: /^v3/ }));
  fireEvent.click(await screen.findByLabelText("lunch_ledger"));

  await waitFor(() => expect(m.patchDraft).toHaveBeenCalledWith(1, 3, { tool_packs: [] }));
});

it("turns one tool off without dropping the pack or the others", async () => {
  signedIn();
  openTheDraft();
  m.patchDraft.mockResolvedValue(DRAFT);
  render(<AdminPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Content" }));
  fireEvent.click(await screen.findByRole("button", { name: /^v3/ }));
  fireEvent.click(await screen.findByRole("button", { name: "2 tools" }));
  fireEvent.click(await screen.findByLabelText("lunch_ledger.void_meal"));

  await waitFor(() =>
    expect(m.patchDraft).toHaveBeenCalledWith(1, 3, {
      tool_packs: [{ pack: "lunch_ledger", tools: { void_meal: { enabled: false } } }],
    }),
  );
});

it("does not offer per-tool overrides for a pack whose tools depend on the turn", async () => {
  signedIn();
  openTheDraft();
  m.catalogue.mockResolvedValue({
    ...CATALOGUE,
    packs: [
      {
        id: "os_admin", version: "1", handles_money: false, evidence: false, dynamic: true,
        framework_managed: false, draft_kinds: [], error: null,
        tool_names: ["cms_get_profile", "cms_publish"],
        tools: [
          { name: "cms_get_profile", description: "read", schema: { type: "object" }, money: false, commit: false, cancel: false },
          { name: "cms_publish", description: "publish", schema: { type: "object" }, money: false, commit: false, cancel: false },
        ],
      },
    ],
  });
  m.version.mockResolvedValue({ ...DRAFT, spec: { ...DRAFT.spec, tool_packs: [{ pack: "os_admin", tools: {} }] } });
  render(<AdminPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Content" }));
  fireEvent.click(await screen.findByRole("button", { name: /^v3/ }));

  expect(await screen.findByText(/cannot be overridden/i)).toBeInTheDocument();
  expect(screen.queryByLabelText("os_admin.cms_publish")).toBeNull();
  expect(screen.queryByRole("button", { name: /tools$/ })).toBeNull();
});

it("warns what gate 2 will ask when a money profile turns on a risky builtin", async () => {
  signedIn();
  openTheDraft();
  m.patchDraft.mockResolvedValue({
    ...DRAFT,
    spec: { ...DRAFT.spec, builtin_tools: ["read", "bash"] },
  });
  render(<AdminPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Content" }));
  fireEvent.click(await screen.findByRole("button", { name: /^v3/ }));
  expect(screen.queryByText(/override reason below/i)).toBeNull();

  fireEvent.click(await screen.findByLabelText("builtin bash"));

  await waitFor(() =>
    expect(m.patchDraft).toHaveBeenCalledWith(1, 3, { builtin_tools: ["read", "bash"] }),
  );
  expect(await screen.findByText(/override reason below/i)).toBeInTheDocument();
});

it("refuses to edit a draft an agent is proposing, and says where to decide it", async () => {
  signedIn();
  m.versions.mockResolvedValue([{ ...DRAFT, actor: "agent:steward", spec: null }]);
  m.versionDiff.mockResolvedValue({ version: 3, against: 2, paths: [], diff: "" });
  m.version.mockResolvedValue({ ...DRAFT, actor: "agent:steward" });
  render(<AdminPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Content" }));
  fireEvent.click(await screen.findByRole("button", { name: /^v3/ }));

  expect(await screen.findByText(/Proposals tab/)).toBeInTheDocument();
  expect(screen.queryByLabelText("lunch_ledger")).toBeNull();
  expect(screen.queryByRole("button", { name: "Publish v3" })).toBeNull();
});

it("creates a source, and warns what a delete does and does not change", async () => {
  signedIn();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  const row = {
    id: 5, kind: "skill", slug: "new-skill", title: "new-skill", body: "", frontmatter: {},
    etag: "e5", updated_by: "hung", updated_at: "2026-09-08T00:00:00Z",
  };
  m.sources.mockResolvedValue([row]);
  render(<AdminPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Content" }));
  fireEvent.change(await screen.findByLabelText("new source slug"), { target: { value: "new-skill" } });
  fireEvent.click(screen.getByRole("button", { name: "Add source" }));

  await waitFor(() =>
    expect(m.putSource).toHaveBeenCalledWith(1, "skill", "new-skill", { title: "new-skill", body: "", frontmatter: {} }),
  );

  fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
  await waitFor(() => expect(m.deleteSource).toHaveBeenCalledWith(1, "skill", "new-skill", "e5"));
  expect(confirm.mock.calls.at(-1)?.[0]).toMatch(/until the next draft snapshots/i);
});

it("rejects a slug the store would refuse, before sending it", async () => {
  signedIn();
  render(<AdminPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Content" }));
  fireEvent.change(await screen.findByLabelText("new source slug"), { target: { value: "Ăn trưa" } });

  expect(await screen.findByText(/lowercase letters, digits/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Add source" })).toBeDisabled();
  expect(m.putSource).not.toHaveBeenCalled();
});

// ----------------------------------------------------- Collections (Phase 13.4)

const ROTA = {
  id: 1, slug: "rota", name: "Rota", description: "who fetches lunch",
  schema: { type: "object", properties: { day: { type: "string" } }, required: ["day"] },
  key: "day", indexed: ["day"], updated_at: "2026-09-08T00:00:00Z",
};

it("shows the three tools a collection generates", async () => {
  signedIn();
  m.collections.mockResolvedValue([ROTA]);
  render(<AdminPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Components" }));

  expect(await screen.findByText("rota_find")).toBeInTheDocument();
  expect(screen.getByText("rota_upsert")).toBeInTheDocument();
  expect(screen.getByText("rota_delete")).toBeInTheDocument();
});

it("says a collection reaches the live bot with no publish, before saving one", async () => {
  signedIn();
  m.collections.mockResolvedValue([]);
  m.putCollection.mockResolvedValue(ROTA);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<AdminPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Components" }));
  fireEvent.change(await screen.findByLabelText("collection slug"), { target: { value: "rota" } });
  fireEvent.change(screen.getByLabelText("collection key"), { target: { value: "day" } });
  fireEvent.change(screen.getByLabelText("collection schema"), {
    target: { value: '{"type":"object","properties":{"day":{"type":"string"}},"required":["day"]}' },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save collection" }));

  await waitFor(() => expect(m.putCollection).toHaveBeenCalled());
  expect(confirm.mock.calls.at(-1)?.[0]).toMatch(/no draft and no publish/i);
  expect(m.putCollection.mock.calls[0][2].key).toBe("day");
});

it("shows the server's own schema error rather than saving something the sidecar would reject", async () => {
  signedIn();
  m.collections.mockResolvedValue([]);
  const { ApiError } = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  m.putCollection.mockRejectedValue(
    new ApiError(422, "schema.day: unsupported JSON Schema keyword 'minimum'"),
  );
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<AdminPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Components" }));
  fireEvent.change(await screen.findByLabelText("collection slug"), { target: { value: "rota" } });
  fireEvent.change(screen.getByLabelText("collection key"), { target: { value: "day" } });
  fireEvent.click(screen.getByRole("button", { name: "Save collection" }));

  expect(await screen.findByText(/unsupported JSON Schema keyword 'minimum'/)).toBeInTheDocument();
});

it("does not send a schema that is not JSON at all", async () => {
  signedIn();
  m.collections.mockResolvedValue([]);
  render(<AdminPage />);

  fireEvent.click(await screen.findByRole("button", { name: "Components" }));
  fireEvent.change(await screen.findByLabelText("collection slug"), { target: { value: "rota" } });
  fireEvent.change(screen.getByLabelText("collection key"), { target: { value: "day" } });
  fireEvent.change(screen.getByLabelText("collection schema"), { target: { value: "{not json" } });
  fireEvent.click(screen.getByRole("button", { name: "Save collection" }));

  expect(await screen.findByText(/not valid JSON/i)).toBeInTheDocument();
  expect(m.putCollection).not.toHaveBeenCalled();
});
