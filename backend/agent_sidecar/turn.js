/**
 * One turn: normalize pi's events to the app's wire format, enforce the caps,
 * assemble the final answer.
 *
 * This is where the logic deleted from `app/agent.py` lives — `_final_answer`,
 * `_split_at_seams`, `_is_narration`, `_strip_narration` — ported *with their
 * comments*, because each of those comments documents a production incident and a
 * comment that explains a bug is worth more than the code above it.
 *
 * The `agent.*` event names are **frozen**: `frontend/src/hooks/use-room.ts:50-89`
 * consumes them, so they are emitted here in final form and `agent.py` forwards
 * them untouched. `app/agui.py` existed only to do this translation and is deleted.
 */

/** Words that mark a fragment as the model narrating itself rather than answering. */
const NARRATION_MARKERS = [
  "mình đọc skill",
  "minh doc skill",
  "mình sẽ đọc",
  "mình làm theo skill",
  "minh lam theo skill",
  "mình làm theo quy trình",
  "theo quy trình ghi",
  "mình đọc",
  "mình sẽ tìm",
  "mình sẽ xem",
  "mình xem skill",
  "mình xem schema",
  "đang đổi tên thành",
  "đã tìm thấy",
  "mình sẽ ghi",
  "tôi sẽ ghi",
  "mình sẽ dùng",
  "reading skill",
  "i'll use the",
  "let me check the skill",
];

/** A fragment is narration when it opens with one of the markers. */
export function isNarration(fragment) {
  const text = (fragment || "").trim().toLowerCase();
  if (!text) return false;
  return NARRATION_MARKERS.some((marker) => text.startsWith(marker) || text.includes(marker));
}

/**
 * Split accumulated assistant text at the seams between "thinking out loud" and
 * the answer. Production emits these as separate sentences glued together with no
 * separator, so a newline or a sentence end is the only seam available.
 */
export function splitAtSeams(text) {
  return (text || "")
    .split(/\n+|(?<=[.!?])\s+/)
    .map((part) => part.trim())
    .filter(Boolean);
}

/**
 * Drop leading narration, but never return empty.
 *
 * A bad reply still beats an empty bubble: if *everything* looks like narration
 * the original is kept, because the room seeing something imperfect is better than
 * the room seeing nothing at all.
 */
export function stripNarration(text) {
  const parts = splitAtSeams(text);
  const kept = parts.filter((part) => !isNarration(part));
  if (!kept.length) return text;
  return kept.join(" ");
}

/**
 * Assemble the final answer from the text fragments of a turn.
 *
 * `fragments` are joined with the **empty string**, never a separator. Joining
 * with one put a blank line between every token and shredded Vietnamese
 * diacritics into "V ẫn không được đâu Kun" in production — a string still visible
 * in the recorded prod corpus (`p142`, `p144`), so this is a live bug, not a
 * historical one.
 *
 * Only text produced *after the last tool call* is the answer: anything before it
 * was the model reasoning toward the call.
 */
export function finalAnswer(fragments, lastToolIndex) {
  const after = fragments.slice(Math.max(0, lastToolIndex));
  const joined = (after.length ? after : fragments).join("");
  return stripNarration(joined).trim();
}

/** Turn caps, as the app has always meant them. */
export const DEFAULT_MAX_TOOLS = 40;
export const DEFAULT_MAX_SECONDS = 120;

/**
 * Run one prompt to completion and return a ready-made `TurnResult`.
 *
 * **A cap breach is a partial answer, not an error.** `agent.py:374-384` logged a
 * warning, cancelled the run and broke the loop: `error` stayed `null`,
 * `final_text` kept everything accumulated so far, and `chat.py:558` posted it as
 * a normal reply. If a cap became an error, every capped turn would flip from a
 * partial reply to a ⚠️ message in the room — a visible regression on the slowest,
 * most complex turns, which are the ones users care about most.
 *
 * @param {object} session an `AgentSession`
 * @param {object} req the `run` command
 * @param {(event: object) => void} emit forwards `agent.*` events verbatim
 */
export async function runTurn(session, req, emit) {
  const turnId = req.turn_id;
  const maxTools = req.max_tools ?? DEFAULT_MAX_TOOLS;
  const maxSeconds = req.max_seconds ?? DEFAULT_MAX_SECONDS;
  const started = Date.now();

  const fragments = [];
  const tools = [];
  let lastToolIndex = 0;
  let capped = false;
  let error = null;
  let deadline = null;

  const cap = (reason) => {
    if (capped) return;
    capped = true;
    // Cancel, but keep everything accumulated so far.
    Promise.resolve(session.abort()).catch(() => {});
    if (typeof req.onCap === "function") req.onCap(reason);
  };

  const unsubscribe = session.subscribe((event) => {
    try {
      switch (event.type) {
        case "agent_start":
          emit({ type: "agent.run.started", turn_id: turnId });
          break;

        case "message_update": {
          const inner = event.assistantMessageEvent;
          if (inner && inner.type === "text_delta" && inner.delta) {
            fragments.push(inner.delta);
            emit({ type: "agent.text.delta", turn_id: turnId, delta: inner.delta });
          }
          break;
        }

        case "tool_execution_start":
          emit({
            type: "agent.tool.start",
            turn_id: turnId,
            call_id: event.toolCallId,
            name: event.toolName,
            args: event.args,
          });
          break;

        case "tool_execution_end": {
          tools.push({
            name: event.toolName,
            call_id: event.toolCallId,
            args: event.args,
            result: event.result,
            is_error: Boolean(event.isError),
          });
          // Text after this point is the answer; text before it was reasoning.
          lastToolIndex = fragments.length;
          emit({
            type: "agent.tool.result",
            turn_id: turnId,
            call_id: event.toolCallId,
            name: event.toolName,
            status: event.isError ? "error" : "completed",
            result: event.result,
          });
          if (tools.length >= maxTools) cap(`max_tools=${maxTools}`);
          break;
        }

        default:
          break;
      }
    } catch (err) {
      // A listener that throws must not take the turn down with it.
      error = error || `event handling failed: ${err && err.message}`;
    }
  });

  try {
    // The deadline **resolves the race**, it does not merely call `abort()`.
    // `session.abort()` stops the provider request but does not necessarily settle
    // the promise `prompt()` returned, so awaiting `prompt()` alone can hang past
    // the cap forever — which is what happened on the first real corpus run: one
    // case sat for minutes with `max_seconds=120` configured. A cap that does not
    // bound the turn is not a cap, and in production that is a hung bot rather
    // than a slow one.
    await Promise.race([
      session.prompt(req.message, buildPromptOptions(req)),
      new Promise((resolve) => {
        deadline = setTimeout(() => {
          cap(`max_seconds=${maxSeconds}`);
          resolve();
        }, maxSeconds * 1000);
      }),
    ]);
  } catch (err) {
    // A cap aborts the run, and the abort surfaces here. That is not an error.
    if (!capped) error = formatError(err);
  } finally {
    if (deadline) clearTimeout(deadline);
    unsubscribe();
  }

  if (error) emit({ type: "agent.run.error", turn_id: turnId, message: error });
  else emit({ type: "agent.run.finished", turn_id: turnId });

  return {
    final_text: finalAnswer(fragments, lastToolIndex),
    tools,
    error,
    capped,
    stats: collectStats(session, tools.length, started),
  };
}

/**
 * `images` reach pi as its own `ImageContent`: **flat `{type, data, mimeType}`**.
 *
 * Not the Anthropic `{source: {type: "base64", mediaType, data}}` nesting this
 * originally sent. `pi-ai`'s `ImageContent` (types.d.ts:241) has `data` and
 * `mimeType` at the top level, so the nested version arrived with both
 * `undefined` and the whole bill vanished: every image case (`B1`–`B3`) came back
 * with **no tools, no text and no error** — the exact silent-drop failure design
 * §12 says must never happen, and one no unit test caught because the tests
 * asserted the shape this function *emitted* rather than the shape pi accepts.
 */
export function buildPromptOptions(req) {
  const images = (req.images || []).map((image) => ({
    type: "image",
    data: image.data,
    mimeType: image.mimeType || image.mime_type || "image/png",
  }));
  return images.length ? { images } : undefined;
}

/**
 * One `error` string, in the shape `chat.py` expects.
 *
 * A provider refusal must not reach the room as the vendor's own text. Production
 * posted "⚠️ Model Blocked This model has been blocked by your team admin settings."
 * as the bot's reply, twice (prod corpus `p271`, `p273`); `chat.py` prefixes the
 * error itself, so the message here stays short and ours.
 */
export function formatError(err) {
  const raw = (err && (err.message || String(err))) || "unknown error";
  const text = raw.replace(/\s+/g, " ").trim();
  if (/model.*(blocked|not permitted|unavailable)/i.test(text)) {
    return "Model không dùng được (bị chặn hoặc hết quyền). Thử lại sau nhé.";
  }
  if (/rate.?limit|429|too many requests/i.test(text)) {
    return "Model đang quá tải (rate limit). Thử lại sau một chút nhé.";
  }
  return text.slice(0, 300);
}

function collectStats(session, toolCalls, started) {
  const elapsed = (Date.now() - started) / 1000;
  let stats = null;
  try {
    stats = typeof session.getSessionStats === "function" ? session.getSessionStats() : null;
  } catch {
    stats = null;
  }
  const tokens = stats && stats.tokens ? stats.tokens : null;
  return {
    // `null` rather than 0 when unknown: `bench.graders.summarize_cost_latency`
    // reports "unknown", and claiming 0 would say "free".
    tokens: tokens ? (tokens.input || 0) + (tokens.output || 0) : null,
    cost: stats && typeof stats.cost === "number" ? stats.cost : null,
    tool_calls: toolCalls,
    elapsed_s: Number(elapsed.toFixed(3)),
  };
}
