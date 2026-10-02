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
import { fileURLToPath } from "node:url";

import { resolveExtensions } from "./extensions.js";
import { toTypeBox } from "./schema.js";

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
/**
 * Models newer than the pinned pi catalogue (`openai/gpt-6.1-sol-pro` is not in
 * 0.84's), registered the way pi documents: a `models.json` merged over the
 * built-in providers. Shipped beside this file so every host gets the same list.
 */
export const MODELS_PATH = fileURLToPath(new URL("./models.json", import.meta.url));

/**
 * The session id every turn runs under. pi 1.0 sends it to OpenRouter as
 * `x-session-id`, which routes requests that share it to the same upstream so the
 * prompt cache (system prompt, skills, tools — the same for every turn) is hit. A
 * session is built per turn, so pi's default — a fresh random id each time — routed
 * every turn cold: the 1.0 benchmark cost about twice 0.84's on the same cases,
 * whose repeats got cheaper as the cache warmed. One constant id restores that.
 */
export const CACHE_AFFINITY_ID = "kernos-sidecar";

export async function buildSession(req, { callTool, modelRuntime } = {}) {
  const runtime = modelRuntime || (await ModelRuntime.create({
    modelsPath: MODELS_PATH,
    modelsStorePath: join(tmpdir(), "kernos-pi-models-store.json"),
  }));
  const model = resolveModel(runtime, req);

  // Both paths are required and must exist: `DefaultResourceLoader` resolves them
  // in its constructor and throws on undefined. `agentDir` is pi's own config
  // directory — nothing of ours lives there, but keeping it stable (under
  // DATA_DIR, passed by Python) lets pi cache its model catalogue instead of
  // refetching it every turn.
  const cwd = ensureDir(req.cwd || join(tmpdir(), "kernos-pi-cwd"));
  const agentDir = ensureDir(req.agent_dir || join(tmpdir(), "kernos-pi-agent"));

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
    sessionManager: SessionManager.inMemory(cwd, { id: CACHE_AFFINITY_ID }),
    ...(settingsManager ? { settingsManager } : {}),
  });

  return { session, model, dispose: () => session.dispose() };
}

/**
 * Pick the model for this turn: the vision model when images are attached.
 *
 * A turn carrying images **must not** fall back to the text model. The primary is
 * often text-only (it was `~deepseek/deepseek-v4-flash-latest`), and a dropped photo means the
 * model invents the total — which is worse than an error, because it is wrong
 * money that looks right. Design §12: fail loudly, never silently drop the photo.
 */
/**
 * Turn a builtin-tool list into pi's tool options.
 *
 * **Empty means money-safety is structural.** `money-safety.mdc` only *asks* the
 * model not to compute money ("do NOT run python/bash to compute
 * money"); without
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
  // The catalogue is `getModels()` in every pi we have run (0.84, 1.0) — look the id up
  // there and only there. This used to try methods by name (`getModel`, `findModel`,
  // `resolveModel`); pi 1.0 added an unrelated `resolveModel(model, messages, options)`
  // that routes virtual models, and calling it with a bare id returned `{}`, a model
  // with no id. A catalogue that does not hold the id is an answer, not a gap: the bare
  // string would send a request we already know fails, and on a vision turn that reads
  // as a dropped photo.
  return runtime.getModels().find((m) => m?.id === id) || null;
}


function ensureDir(path) {
  mkdirSync(path, { recursive: true });
  return path;
}
