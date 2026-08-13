/**
 * The JSONL RPC loop: one JSON object per line over stdin/stdout.
 *
 *   Python → run · tool_result · summarize · ping
 *   sidecar → agent.* (verbatim) · tool_call · turn_done · summarize_done · pong · fatal
 *
 * **Every command carries a `req_id` and every reply echoes it.** That is not
 * decoration: `/internal/bridge-smoke` (`main.py:676`) is *not* under
 * `chat._agent_lock`, so a `ping` can legitimately arrive mid-turn and interleave
 * on one stdout.
 *
 * Framing reads **bytes and splits on `\n`** rather than using a line reader. Pi's
 * own `docs/rpc.md` warns that generic Unicode line readers are incompatible with
 * this protocol, and a Vietnamese payload is exactly where that bites.
 *
 * One malformed line must never take the process down: the bot would then be dead
 * until the next deploy over a single bad turn.
 */
import { buildSession } from "./session.js";
import { runTurn } from "./turn.js";

/** Resolvers for in-flight `tool_call`s, keyed by the pi tool-call id. */
const pendingToolCalls = new Map();

export function createDispatcher({ write, buildSessionFn = buildSession, runTurnFn = runTurn } = {}) {
  const emit = (message) => write(message);

  /**
   * Forward an `agent.*` event, stamped with the `req_id` it belongs to.
   *
   * The stamp is not optional: `pi_bridge` routes every line by `req_id`, so an
   * unstamped event has no queue to land in and is dropped. The symptom is a turn
   * that works perfectly and a room whose live timeline stays empty — which is
   * exactly what happened, and no stubbed unit test noticed because they asserted
   * event *order* rather than routing.
   */
  const emitEvent = (reqId) => (event) => write({ ...event, req_id: reqId });

  /** Ask Python to run a tool, and block this call until `tool_result` arrives. */
  const callTool = (reqId) => (callId, name, args) =>
    new Promise((resolve, reject) => {
      pendingToolCalls.set(callId, { resolve, reject });
      emit({ type: "tool_call", req_id: reqId, call_id: callId, name, args });
    });

  async function handle(command) {
    const reqId = command.req_id;
    switch (command.type) {
      case "ping": {
        emit({ type: "pong", req_id: reqId, elapsed_s: 0, text: "pong" });
        return;
      }

      case "tool_result": {
        const pending = pendingToolCalls.get(command.call_id);
        if (!pending) {
          // Nothing is blocked on it. Report rather than swallow: a stray result
          // means the correlation is broken somewhere upstream.
          emit({ type: "fatal", req_id: reqId, message: `no pending tool call ${command.call_id}` });
          return;
        }
        pendingToolCalls.delete(command.call_id);
        let payload = command.content;
        if (typeof payload === "string") {
          try {
            payload = JSON.parse(payload);
          } catch {
            /* a non-JSON result is passed through as text */
          }
        }
        pending.resolve(payload);
        return;
      }

      case "run": {
        let built = null;
        try {
          built = await buildSessionFn(command, { callTool: callTool(reqId) });
          const result = await runTurnFn(built.session, command, emitEvent(reqId));
          emit({ type: "turn_done", req_id: reqId, turn_id: command.turn_id, ...result });
        } catch (err) {
          // Setup failure still has to close the turn, or `chat.py` waits forever
          // and the room sees nothing at all.
          emitEvent(reqId)({
            type: "agent.run.error",
            turn_id: command.turn_id,
            message: String((err && err.message) || err),
          });
          emit({
            type: "turn_done",
            req_id: reqId,
            turn_id: command.turn_id,
            final_text: "",
            tools: [],
            error: String((err && err.message) || err),
            capped: false,
            stats: null,
          });
        } finally {
          try {
            built?.dispose?.();
          } catch {
            /* disposing a half-built session is best effort */
          }
        }
        return;
      }

      case "summarize": {
        // Specified, never inherited. `summarize.py:51-56` ran with no tools, no
        // setting_sources and the summary prompt as the entire message. Inheriting
        // the `run` session's construction would change every room's long-term
        // memory flavor with no test catching it.
        let built = null;
        try {
          built = await buildSessionFn(
            {
              ...command,
              tools: [],
              skills: [],
              context_files: [],
              system: "",
              images: [],
            },
            { callTool: () => Promise.reject(new Error("summarize has no tools")) },
          );
          const result = await runTurnFn(built.session, { ...command, message: command.text },
            () => {});
          emit({ type: "summarize_done", req_id: reqId, text: result.final_text || "" });
        } catch (err) {
          // A failed summary must never crash a turn; Python turns "" into a no-op.
          emit({ type: "summarize_done", req_id: reqId, text: "",
                 error: String((err && err.message) || err) });
        } finally {
          try {
            built?.dispose?.();
          } catch {
            /* best effort */
          }
        }
        return;
      }

      default:
        emit({ type: "fatal", req_id: reqId, message: `unknown command ${command.type}` });
    }
  }

  return { handle, pendingToolCalls };
}

/**
 * Feed raw stdin bytes in, get parsed commands out.
 *
 * Splits on `\n` over a byte buffer, so a line arriving in two chunks is handled
 * and a `\r\n` ending is tolerated.
 */
export function createLineReader(onLine, onMalformed) {
  let buffer = Buffer.alloc(0);
  return (chunk) => {
    buffer = Buffer.concat([buffer, Buffer.from(chunk)]);
    let newline = buffer.indexOf(0x0a);
    while (newline !== -1) {
      const raw = buffer.subarray(0, newline).toString("utf8").replace(/\r$/, "");
      buffer = buffer.subarray(newline + 1);
      const text = raw.trim();
      if (text) {
        try {
          onLine(JSON.parse(text));
        } catch (err) {
          onMalformed(text, err);
        }
      }
      newline = buffer.indexOf(0x0a);
    }
  };
}

export function main() {
  const write = (message) => {
    process.stdout.write(`${JSON.stringify(message)}\n`);
  };
  const { handle } = createDispatcher({ write });

  const feed = createLineReader(
    (command) => {
      // Deliberately not awaited: a `run` blocked on a `tool_call` must not stop
      // a concurrent `ping` from being answered on the same stdout.
      Promise.resolve(handle(command)).catch((err) => {
        write({ type: "fatal", req_id: command.req_id, message: String((err && err.message) || err) });
      });
    },
    (text, err) => {
      // One bad line is one bad turn, not a dead bot until the next deploy.
      write({ type: "fatal", req_id: null, message: `malformed line: ${err.message}` });
    },
  );

  process.stdin.on("data", feed);
  process.stdin.on("end", () => process.exit(0));

  // A crash after this point would leave Python waiting forever, so say so first.
  process.on("uncaughtException", (err) => {
    write({ type: "fatal", req_id: null, message: `uncaught: ${err && err.message}` });
  });
  process.on("unhandledRejection", (err) => {
    write({ type: "fatal", req_id: null, message: `unhandled: ${err && err.message}` });
  });
}

if (import.meta.url === `file://${process.argv[1]}`) main();
