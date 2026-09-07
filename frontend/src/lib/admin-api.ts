/** The operator's client for `/api/admin/*` (plan Phase 12.1).
 *
 * Deliberately separate from `./api`:
 *
 *  - `./api` attaches the room's bearer token to every request. That token must never
 *    reach an admin route, and `ADMIN_PASSWORD` — one shared secret with full power over
 *    every business, profile and version — must never reach a room route. Two clients,
 *    two header sets, one test asserting both directions.
 *  - The credential lives in `sessionStorage`, not `localStorage`: it goes when the tab
 *    goes. "Sign out" clears it now. It is never put in a URL and never rendered.
 *
 * `/api/*` is network-only in the service worker, so no admin response is cached.
 */
import { ApiError } from "./api";

const PW = "chiatienan.admin_pw";
const ACTOR = "chiatienan.admin_actor";

export type Cred = { password: string; actor: string };

/** Memory is the source of truth; sessionStorage is how a reload survives. */
let cred: Cred | null = null;

const store = (): Storage | null => {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null; // Safari private mode throws on access, not on use
  }
};

export function loadCred(): Cred | null {
  if (cred) return cred;
  const s = store();
  const password = s?.getItem(PW);
  if (!password) return null;
  cred = { password, actor: s?.getItem(ACTOR) || "admin" };
  return cred;
}

export function setCred(next: Cred): void {
  cred = next;
  const s = store();
  try {
    s?.setItem(PW, next.password);
    s?.setItem(ACTOR, next.actor);
  } catch {
    /* storage full or blocked — the in-memory credential still works for this page */
  }
}

export function clearCred(): void {
  cred = null;
  const s = store();
  try {
    s?.removeItem(PW);
    s?.removeItem(ACTOR);
  } catch {
    /* nothing to undo */
  }
}

/** A gate failure is 422 with `{detail: {gates: [{gate, message}]}}`. */
export type Gate = { gate: string; message: string };

export class GateFailure extends Error {
  constructor(public gates: Gate[]) {
    super(gates.map((g) => `${g.gate}: ${g.message}`).join("\n"));
    this.name = "GateFailure";
  }
}

async function req(path: string, init: RequestInit = {}): Promise<any> {
  const c = loadCred();
  if (!c) throw new ApiError(401, "not signed in");
  const headers: Record<string, string> = {
    "content-type": "application/json",
    "X-Admin-Password": c.password,
    "X-Actor": c.actor,
    ...(init.headers as any),
  };
  const res = await fetch(`/api/admin${path}`, { ...init, headers });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}) as any);
    const gates = body?.detail?.gates;
    if (Array.isArray(gates) && gates.length) throw new GateFailure(gates);
    const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? {});
    throw new ApiError(res.status, detail || res.statusText);
  }
  if (res.status === 204) return null;
  const etag = res.headers.get("ETag");
  const json = await res.json();
  return etag && json && typeof json === "object" && !Array.isArray(json) ? { ...json, etag } : json;
}

/** Does this password work? The cheapest authenticated GET there is. */
export const signIn = async (next: Cred): Promise<void> => {
  setCred(next);
  try {
    await req("/businesses");
  } catch (e) {
    clearCred();
    throw e;
  }
};

// ---------------------------------------------------------------- what is live
export type Business = { id: number; slug: string; name: string; tool_packs: string[] };
export type Profile = {
  id: number; business_id: number; name: string; managed_by: string;
  published_version_id: number | null;
};
export type Agent = {
  id: number; business_id: number; slug: string; name: string; role: string;
  is_default: boolean; profile_id: number; delegates_to: number[];
  capabilities: Record<string, any>; description: string | null;
};
export type Binding = { space_id: string; agent_id: number; overrides: Record<string, any>; updated_at: string };

export const businesses = (): Promise<Business[]> => req("/businesses");
export const profiles = (): Promise<Profile[]> => req("/profiles");
export const agents = (): Promise<Agent[]> => req("/agents");
export const bindings = (): Promise<Binding[]> => req("/bindings");

export const bind = (spaceId: string, agentId: number) =>
  req(`/spaces/${encodeURIComponent(spaceId)}/binding`, {
    method: "PUT",
    body: JSON.stringify({ agent_id: agentId }),
  });
export const unbind = (spaceId: string) =>
  req(`/spaces/${encodeURIComponent(spaceId)}/binding`, { method: "DELETE" });

/** What a space actually runs, resolved: the spec, the engine half, the pipeline. */
export const resolved = (spaceId: string): Promise<{ spec: any; pipeline: any }> =>
  req(`/spaces/${encodeURIComponent(spaceId)}/resolved`);

export const patchAgent = (agentId: number, patch: Record<string, any>): Promise<Agent> =>
  req(`/agents/${agentId}`, { method: "PATCH", body: JSON.stringify(patch) });

// -------------------------------------------------------------------- sources
export type Source = {
  id: number; kind: string; slug: string; title: string; body: string;
  frontmatter: Record<string, any>; etag: string; updated_by: string; updated_at: string;
};

export const sources = (businessId: number): Promise<Source[]> =>
  req(`/businesses/${businessId}/sources`);
export const putSource = (
  businessId: number,
  kind: string,
  slug: string,
  body: { title?: string; body: string; frontmatter?: Record<string, any> },
  etag: string,
): Promise<Source> =>
  req(`/businesses/${businessId}/sources/${kind}/${encodeURIComponent(slug)}`, {
    method: "PUT",
    body: JSON.stringify(body),
    headers: { "If-Match": etag },
  });

// ------------------------------------------------------------------- versions
export type Version = {
  id: number; version: number; status: string; actor: string;
  note: string | null; created_at: string; published_at: string | null; spec: any;
};
export type Diff = { version: number; against: number | null; paths: string[]; diff: string };

export const versions = (profileId: number): Promise<Version[]> =>
  req(`/profiles/${profileId}/versions`);
export const version = (profileId: number, v: number): Promise<Version> =>
  req(`/profiles/${profileId}/versions/${v}`);
export const versionDiff = (profileId: number, v: number, against?: number): Promise<Diff> =>
  req(`/profiles/${profileId}/versions/${v}/diff${against === undefined ? "" : `?against=${against}`}`);
/** A draft of `profileId`. `from_version` copies that version's spec; without it the
 * draft snapshots the business's *current* sources — which is what makes a source edit
 * reach the bot. There is no rollback button on purpose: `POST /rollback` re-publishes
 * the same row and overwrites its note and timestamp, so a version's history stops being
 * a record. "New draft from vN" then Publish reaches the same place, additively, and
 * shows the operator the diff before they commit to it. */
export const createDraft = (
  profileId: number,
  body: { from_version?: number; note?: string } = {},
): Promise<Version> =>
  req(`/profiles/${profileId}/versions`, { method: "POST", body: JSON.stringify(body) });

export const patchDraft = (profileId: number, v: number, patch: Record<string, any>): Promise<Version> =>
  req(`/profiles/${profileId}/versions/${v}`, { method: "PATCH", body: JSON.stringify(patch) });
export const publish = (profileId: number, v: number, body: { note?: string; override_reason?: string }) =>
  req(`/profiles/${profileId}/versions/${v}/publish`, { method: "POST", body: JSON.stringify(body) });
export const retire = (profileId: number, v: number) =>
  req(`/profiles/${profileId}/versions/${v}/retire`, { method: "POST" });

// ------------------------------------------------------------------ proposals
export type Proposal = {
  id: number; business_id: number; agent_id: number; profile_id: number; version_id: number;
  rationale: string; diff: Record<string, any>; source_changes: any[]; status: string;
  decided_by: string | null; decided_at: string | null; last_error: string | null; created_at: string;
};

export const proposals = (status?: string): Promise<Proposal[]> =>
  req(`/proposals${status ? `?status=${status}` : ""}`);
export const approveProposal = (id: number) => req(`/proposals/${id}/approve`, { method: "POST" });
export const rejectProposal = (id: number) => req(`/proposals/${id}/reject`, { method: "POST" });

// ---------------------------------------------------------------------- audit
export type AuditRow = {
  id: number; actor: string; action: string; entity: string; entity_id: string;
  before: any; after: any; at: string;
};

export const audit = (limit = 100): Promise<AuditRow[]> => req(`/audit?limit=${limit}`);
