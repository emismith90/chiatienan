/**
 * The sidecar's extension registry (design §4.1, right column).
 *
 * A profile may name sidecar extensions by id with a JSON config; each id maps to
 * a factory `(pi, config) => void` registered here. The registry is EMPTY in
 * Phase 1 — this file exists so `session.js` can accept the `extensions` field of
 * the `run` command now, and so an unknown id fails the turn loudly instead of
 * silently running without the policy the profile asked for.
 */
const REGISTRY = new Map();

/** Register a factory under an id. Re-registering the same id replaces it. */
export function registerExtension(id, factory) {
  if (typeof id !== "string" || !id) throw new Error("extension id must be a non-empty string");
  if (typeof factory !== "function") throw new Error(`extension ${id}: factory must be a function`);
  REGISTRY.set(id, factory);
}

export function knownExtensions() {
  return [...REGISTRY.keys()].sort();
}

/**
 * Resolve the `extensions` entries of a `run` command into pi `InlineExtension`s.
 *
 * Accepts `"id"` or `{ id, config }`. Unknown ids throw: a profile that names a
 * policy the sidecar does not ship must not run as if it had.
 */
export function resolveExtensions(entries) {
  const out = [];
  for (const entry of entries || []) {
    const id = typeof entry === "string" ? entry : entry?.id;
    const factory = REGISTRY.get(id);
    if (!factory) throw new Error(`unknown sidecar extension ${JSON.stringify(id)}`);
    const config = (entry && typeof entry === "object" && entry.config) || {};
    out.push({ name: id, factory: (pi) => factory(pi, config) });
  }
  return out;
}

/** Test seam. */
export function _resetExtensions() {
  REGISTRY.clear();
}
