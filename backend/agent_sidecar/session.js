/**
 * Session construction, and the proxy that sends every tool call back to Python.
 *
 * The Node side owns the whole harness — provider, model, resources, events. It
 * owns **no arithmetic**: all money tools execute in Python over the real DB, so a
 * number never crosses to the model's side of the wire (design D3).
 */
import { createAgentSession, defineTool, DefaultResourceLoader, ModelRuntime, SessionManager,
  SettingsManager } from "@earendil-works/pi-coding-agent";

import { mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { resolveExtensions } from "./extensions.js";
import { toTypeBox } from "./schema.js";

/** Constant, not configurable. One less knob to get wrong in prod. */
export const OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1";

export const KEY_ENV = "OPEN_ROUTER_KEY";

/**
 * Wrap one Python tool as a pi tool.
 *
 * **`{ok: false}` is a content block, NOT a throw.** `tools.py:8` is explicit:
 *
 * > Validation failures are returned as `{"ok": False, "error": ...}` dicts (a
 * > clarifying-question result) rather than raised, so the model can ask the user
 * > instead of guessing.
 *
 * Pi's own convention is "always throw to signal failure", and following it here
 * would turn every "which day did you mean?" into an error the model apologizes
 * for instead of a question it asks — a corpus-wide regression no unit test would
 * catch. **Only transport death throws.**
 */
export function proxyTool(spec, callTool) {
  return defineTool({
    name: spec.name,
    label: spec.label || spec.name,
    description: spec.description,
    parameters: toTypeBox(spec.schema, spec.name),
    execute: async (toolCallId, params) => {
      // A throw here is the bridge dying, and it must propagate: pretending a
      // dead bridge returned a result would let the turn continue on a lie.
      const result = await callTool(toolCallId, spec.name, params);
      return {
        content: [{ type: "text", text: JSON.stringify(result) }],
        details: result,
      };
    },
  });
}

/**
 * Skill bodies and context files, as inline `agentsFiles`.
 *
 * Design §5 hoped skills could be injected in memory. They cannot: pi's
 * `skillsOverride` takes a descriptor with a **`filePath`** pointing at a real
 * file, and it puts a skill's *name and description* in the system prompt while
 * expecting the agent to `read` the body when a task matches. With the built-in
 * tools disabled there is no `read`, so the model would never see a single
 * procedure body — a silent, corpus-wide regression in exactly the money workflows
 * the skills encode.
 *
 * So design §5.1's stated fallback is the implementation: every skill body ships
 * as an `agentsFiles` entry, which *does* take inline `content` and lands in every
 * system prompt. ~8KB, always present, nothing written to disk — which also
 * deletes `skills.py` and the whole bug class its `_prune` existed for.
 */
export function buildAgentsFiles(req) {
  const files = [];
  for (const file of req.context_files || []) {
    files.push({ path: `/virtual/${file.path}`, content: file.content });
  }
  for (const skill of req.skills || []) {
    files.push({
      path: `/virtual/skills/${skill.name}.md`,
      content: `# Skill: ${skill.name}\n\n${skill.description || ""}\n\n${skill.body || ""}`,
    });
  }
  return files;
}

/**
 * Build a fresh session for one turn.
 *
 * Fresh per turn on purpose: continuity already comes from `chat.build_history`
 * plus `memory.md` injected as text, and a persistent pi session would double it.
 * The *process* is long-lived, so the spawn cost is paid once.
 */
export async function buildSession(req, { callTool, modelRuntime } = {}) {
  const runtime = modelRuntime || (await ModelRuntime.create());
  const model = resolveModel(runtime, req);

  // Both paths are required and must exist: `DefaultResourceLoader` resolves them
  // in its constructor and throws on undefined. `agentDir` is pi's own config
  // directory — nothing of ours lives there, but keeping it stable (under
  // DATA_DIR, passed by Python) lets pi cache its model catalogue instead of
  // refetching it every turn.
  const cwd = ensureDir(req.cwd || join(tmpdir(), "chiatienan-pi-cwd"));
  const agentDir = ensureDir(req.agent_dir || join(tmpdir(), "chiatienan-pi-agent"));

  // Both are optional and absent from every command today (plan Task 1.4): a
  // profile's `settings` become an in-memory SettingsManager (nothing on disk to
  // drift), and its `extensions` resolve against the sidecar's registry.
  const settingsManager = buildSettingsManager(req.settings);
  const extensionFactories = resolveExtensions(req.extensions);

  const loader = new DefaultResourceLoader({
    cwd,
    agentDir,
    systemPromptOverride: () => req.system || "",
    agentsFilesOverride: () => ({ agentsFiles: buildAgentsFiles(req) }),
    ...(settingsManager ? { settingsManager } : {}),
    ...(extensionFactories.length ? { extensionFactories } : {}),
  });
  await loader.reload();

  const customTools = (req.tools || []).map((spec) => proxyTool(spec, callTool));

  const { session } = await createAgentSession({
    resourceLoader: loader,
    modelRuntime: runtime,
    model,
    thinkingLevel: req.thinking || "medium",
    customTools,
    ...toolOptionsFor(req.builtin_tools, customTools.map((tool) => tool.name)),
    sessionManager: SessionManager.inMemory(cwd),
    ...(settingsManager ? { settingsManager } : {}),
  });

  return { session, model, dispose: () => session.dispose() };
}

/**
 * Pick the model for this turn: the vision model when images are attached.
 *
 * A turn carrying images **must not** fall back to the text model. The primary is
 * text-only (`~deepseek/deepseek-v4-flash-latest`), so a dropped photo means the
 * model invents the total — which is worse than an error, because it is wrong
 * money that looks right. Design §12: fail loudly, never silently drop the photo.
 */
/**
 * Turn a builtin-tool list into pi's tool options.
 *
 * **Empty means money-safety is structural.** `money-safety.mdc` only *asks* the
 * model not to compute money ("KHÔNG chạy python/bash để tính tiền"); without
 * `bash` it cannot. Enabling the builtins trades that guarantee for the model being
 * able to work things out itself, and restores the mechanism behind a known
 * production defect — `moneyguard`'s field note records the one non-image unbacked
 * case as "a split it computed with bash", and the recorded prod corpus holds a
 * reply with a hand-typed six-row balance table (`p30`).
 *
 * That trade is a configuration decision, and the benchmark measures its cost:
 * `grade_prose`'s stage 1 is `moneyguard.unbacked_amounts`, which fails any reply
 * stating a number no tool produced.
 *
 * pi treats `tools` as an **allowlist**, so the custom names must be repeated there
 * or all money tools vanish and the model is left holding only `bash` — the worst
 * of both worlds.
 */
export function toolOptionsFor(builtins, customNames) {
  const enabled = builtins || [];
  if (!enabled.length) return { noTools: "builtin" };
  return { tools: [...enabled, ...(customNames || [])] };
}


/**
 * A profile's pi `settings` block → `SettingsManager.inMemory`, or `null` when the
 * command carries none (today's commands), so nothing about session construction
 * changes for a profile that does not set it.
 */
export function buildSettingsManager(settings) {
  if (!settings || typeof settings !== "object" || Array.isArray(settings)) return null;
  if (!Object.keys(settings).length) return null;
  return SettingsManager.inMemory(settings);
}

export function resolveModel(runtime, req) {
  const hasImages = Boolean(req.images && req.images.length);
  const wanted = hasImages ? req.vision_model : req.model;

  if (hasImages && !wanted) {
    throw new Error(
      "This turn has images but PI_VISION_MODEL is not set. Refusing to run: the " +
        "text model cannot see the bill and would invent a total.",
    );
  }
  if (!wanted) throw new Error("PI_MODEL is not set");

  const model = findModel(runtime, wanted);
  if (!model) {
    throw new Error(
      `model ${wanted} is not available from this provider` +
        (hasImages ? " (vision turn — not falling back to the text model)" : ""),
    );
  }
  return model;
}

function findModel(runtime, id) {
  // `ModelRuntime` exposes its catalogue differently across versions, so try the
  // documented shapes in turn.
  for (const getter of ["getModel", "findModel", "resolveModel"]) {
    if (typeof runtime?.[getter] === "function") {
      try {
        const found = runtime[getter](id);
        if (found) return found;
      } catch {
        /* try the next shape */
      }
    }
  }
  const list = typeof runtime?.getModels === "function" ? runtime.getModels() : runtime?.models;
  if (Array.isArray(list)) {
    const found = list.find((m) => m?.id === id || m?.model === id);
    // A catalogue that exists and does not contain the id is an answer, not a
    // gap: returning the bare string here would send a request we already know
    // fails, and on a vision turn that reads as a dropped photo.
    return found || null;
  }
  // No catalogue to check against — providers accept a bare id, so let it through
  // rather than refusing to run on a version whose API we could not read.
  return id;
}


function ensureDir(path) {
  mkdirSync(path, { recursive: true });
  return path;
}
