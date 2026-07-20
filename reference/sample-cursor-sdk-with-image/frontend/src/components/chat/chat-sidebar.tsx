"use client";

import { useState, useRef, useEffect, useCallback, useLayoutEffect, useMemo, type FormEvent } from "react";
import { MessageSquare, X, Send, Square, Pin, PinOff, Check, Loader2, XCircle, Plus, History, Copy, Bug, ImagePlus } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useChat, type ChatMessage, type ChatImage, type ToolCallEntry } from "@/hooks/use-chat";
import { useChatSidebarState, DEFAULT_WIDTH } from "@/hooks/use-chat-sidebar";
import { useConversations } from "@/hooks/use-conversations";
import { ConversationList } from "@/components/chat/conversation-list";
import {
  AgentResponseRenderer,
  parseRenderables,
  type SuggestedActionsData,
} from "@/components/chat/chat-renderers";
import { modelsUrl } from "@/lib/api";

/* ── Image attachments ────────────────────────────────────── */
const IMAGE_ACCEPT = "image/png,image/jpeg,image/webp,image/gif";
const IMAGE_ALLOWED = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
const IMAGE_MAX_BYTES = 5 * 1024 * 1024; // 5 MB per image
const IMAGE_MAX_COUNT = 4;
const IMAGE_MAX_TOTAL_BYTES = 6 * 1024 * 1024;

/** Preview/data URL for a ChatImage. Empty when data was stripped for storage. */
function imageSrc(img: ChatImage): string {
  return img.data ? `data:${img.mimeType};base64,${img.data}` : "";
}

/** Read a File into a ChatImage (raw base64, no data: prefix). Returns null if
 * it isn't an allowed image type or exceeds the size cap. */
function fileToChatImage(file: File): Promise<ChatImage | null> {
  if (!IMAGE_ALLOWED.has(file.type) || file.size > IMAGE_MAX_BYTES) {
    return Promise.resolve(null);
  }
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      const comma = result.indexOf(","); // strip "data:<mime>;base64," prefix
      const data = comma >= 0 ? result.slice(comma + 1) : result;
      resolve(data ? { data, mimeType: file.type, name: file.name } : null);
    };
    reader.onerror = () => resolve(null);
    reader.readAsDataURL(file);
  });
}

/* ── Tool Call Timeline ─────────────────────────────────────── */

function ToolCallResult({ result }: { result: string }) {
  try {
    const parsed = JSON.parse(result);
    if (parsed.error) {
      return <div className="text-[10px] text-destructive px-1">Error: {parsed.error}</div>;
    }
    const { columns, rows, row_count, truncated } = parsed;
    if (!columns && !parsed.error) {
      const text = typeof parsed === "string" ? parsed : JSON.stringify(parsed, null, 2);
      return <div className="text-[10px] text-muted-foreground px-1 whitespace-pre-wrap max-h-24 overflow-y-auto">{text.slice(0, 500)}</div>;
    }
    if (!columns || !rows) {
      return <div className="text-[10px] text-muted-foreground px-1">{result.slice(0, 200)}</div>;
    }
    const displayRows = rows.slice(0, 10);
    return (
      <div className="space-y-1">
        <div className="text-[10px] text-muted-foreground px-1">
          {row_count} row{row_count !== 1 ? "s" : ""}{truncated ? " (truncated)" : ""}
        </div>
        <div className="overflow-x-auto">
          <table className="text-[10px] font-mono border-collapse w-full">
            <thead>
              <tr>
                {columns.map((col: string) => (
                  <th key={col} className="text-left px-1.5 py-0.5 border-b border-border text-muted-foreground font-medium whitespace-nowrap">
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {displayRows.map((row: Record<string, unknown>, i: number) => (
                <tr key={i}>
                  {columns.map((col: string) => (
                    <td key={col} className="px-1.5 py-0.5 whitespace-nowrap border-b border-border/50">
                      {row[col] != null ? String(row[col]) : ""}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length > 10 && (
            <div className="text-[10px] text-muted-foreground px-1 py-0.5">...and {rows.length - 10} more rows</div>
          )}
        </div>
      </div>
    );
  } catch {
    return <div className="text-[10px] text-muted-foreground px-1">{result.slice(0, 200)}</div>;
  }
}

const TOOL_LABELS: Record<string, string> = {
  get_current_time: "Checking the time",
};

const TOOL_ACRONYMS = new Set(["ai", "api", "sow", "sql", "dau", "cli", "csv", "mcp", "id"]);

function humanizeToolName(name: string): string {
  const base = name.replace(/^mcp__[^_]+__/, "").replace(/^mcp__/, "");
  const words = base.split("_").filter(Boolean);
  if (words.length === 0) return name;
  return words
    .map((w, i) =>
      TOOL_ACRONYMS.has(w)
        ? w.toUpperCase()
        : i === 0
          ? w.charAt(0).toUpperCase() + w.slice(1)
          : w,
    )
    .join(" ");
}

function ToolCallNode({ entry, isLast, detailed }: { entry: ToolCallEntry; isLast: boolean; detailed: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const [liveElapsed, setLiveElapsed] = useState<number | null>(null);
  useEffect(() => {
    if (entry.status !== "running" || !entry.startedAt) return;
    const tick = () => setLiveElapsed((Date.now() - entry.startedAt) / 1000);
    tick();
    const id = setInterval(tick, 100);
    return () => clearInterval(id);
  }, [entry.status, entry.startedAt]);

  const durationSec =
    entry.endedAt && entry.startedAt
      ? (entry.endedAt - entry.startedAt) / 1000
      : entry.status === "running"
        ? liveElapsed
        : null;
  const showDuration = durationSec !== null && durationSec >= 0;

  let argsDisplay = "";
  if (detailed && entry.args) {
    try {
      const parsed = JSON.parse(entry.args);
      argsDisplay = parsed.sql ?? parsed.code ?? parsed.skill_name ?? parsed.ref_name ?? JSON.stringify(parsed, null, 2);
    } catch {
      argsDisplay = entry.args;
    }
  }

  const label = detailed ? entry.name : (TOOL_LABELS[entry.name] ?? humanizeToolName(entry.name));

  return (
    <div className="relative pl-5">
      {!isLast && (
        <div className="absolute left-1.75 top-5 bottom-0 w-0.5 bg-border" />
      )}

      <div className="absolute left-0 top-0.5">
        {entry.status === "running" ? (
          <Loader2 className="h-4 w-4 text-yellow-500 animate-spin" />
        ) : entry.status === "error" ? (
          <XCircle className="h-4 w-4 text-destructive" />
        ) : (
          <Check className="h-4 w-4 text-green-500" />
        )}
      </div>

      <button
        type="button"
        className={`flex items-center gap-1.5 text-xs text-muted-foreground py-0.5 ${detailed ? "hover:text-foreground transition-colors cursor-pointer" : "cursor-default"}`}
        onClick={detailed ? () => setExpanded(!expanded) : undefined}
      >
        <span className={detailed ? "font-mono" : ""}>{label}</span>
        {showDuration && (
          <span className="text-[10px] px-1 py-0.5 rounded bg-muted tabular-nums">
            {durationSec! < 0.1 ? "<0.1s" : `${durationSec!.toFixed(1)}s`}
          </span>
        )}
        {entry.status === "running" && (
          <span className="text-yellow-500">{detailed ? "running..." : ""}</span>
        )}
        {detailed && <span className="text-[10px]">{expanded ? "▼" : "▶"}</span>}
      </button>

      {detailed && expanded && (
        <div className="mt-1 mb-2 space-y-1.5">
          {argsDisplay && (
            <pre className="text-[10px] leading-snug bg-muted rounded p-2 overflow-x-auto max-h-40 whitespace-pre-wrap font-mono">
              {argsDisplay}
            </pre>
          )}
          {entry.result && <ToolCallResult result={entry.result} />}
        </div>
      )}
    </div>
  );
}

function ToolCallTimeline({ toolCalls, detailed }: { toolCalls: ToolCallEntry[]; detailed: boolean }) {
  return (
    <div className="my-1.5">
      {toolCalls.map((tc, i) => (
        <ToolCallNode key={tc.id} entry={tc} isLast={i === toolCalls.length - 1} detailed={detailed} />
      ))}
    </div>
  );
}

/* ── Typing effect hook ─────────────────────────────────────── */

const CHARS_PER_FRAME = 4;

function useTypingEffect(target: string, isStreaming: boolean): string {
  const [displayed, setDisplayed] = useState("");
  const targetRef = useRef(target);
  const posRef = useRef(0);
  const rafRef = useRef(0);
  const runningRef = useRef(false);

  targetRef.current = target;

  useLayoutEffect(() => {
    if (!isStreaming) {
      cancelAnimationFrame(rafRef.current);
      runningRef.current = false;
      posRef.current = target.length;
      setDisplayed(target);
      return;
    }

    if (runningRef.current) return;
    runningRef.current = true;

    function tick() {
      if (!runningRef.current) return;
      const t = targetRef.current;
      if (posRef.current < t.length) {
        posRef.current = Math.min(posRef.current + CHARS_PER_FRAME, t.length);
        setDisplayed(t.slice(0, posRef.current));
      }
      rafRef.current = requestAnimationFrame(tick);
    }

    rafRef.current = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(rafRef.current);
      runningRef.current = false;
    };
  }, [isStreaming]);

  return displayed;
}

/* ── Reasoning ("Thinking") bubble ──────────────────────────── */

function ReasoningBubble({ content, streaming }: { content: string; streaming: boolean }) {
  const [open, setOpen] = useState(false);

  if (!content) {
    if (!streaming) return null;
    return (
      <div className="my-1 flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        <span>Thinking…</span>
      </div>
    );
  }

  const show = open || streaming;
  return (
    <div className="my-1">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
      >
        {streaming ? <Loader2 className="h-3 w-3 animate-spin" /> : <span>💭</span>}
        <span>{streaming ? "Thinking…" : "Thought process"}</span>
        <span className="text-[10px]">{show ? "▼" : "▶"}</span>
      </button>
      {show && (
        <div className="mt-1 ml-1 border-l-2 border-border pl-2 text-[11px] leading-snug text-muted-foreground whitespace-pre-wrap max-h-64 overflow-y-auto">
          {content}
        </div>
      )}
    </div>
  );
}

/* ── Message Bubble ─────────────────────────────────────────── */

function MessageBubble({
  message,
  onActionClick,
  actionsEnabled,
  showActions,
}: {
  message: ChatMessage;
  onActionClick?: (action: string) => void;
  actionsEnabled: boolean;
  showActions?: boolean;
}) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="flex max-w-[85%] flex-col items-end gap-1.5">
          {message.images && message.images.length > 0 && (
            <div className="flex flex-wrap justify-end gap-1.5">
              {message.images.map((img, i) =>
                imageSrc(img) ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    key={i}
                    src={imageSrc(img)}
                    alt={img.name || "attachment"}
                    className="max-h-40 rounded-lg border border-border object-contain"
                  />
                ) : (
                  <div
                    key={i}
                    className="flex h-16 w-16 items-center justify-center rounded-lg border border-border bg-muted text-[10px] text-muted-foreground"
                    title={img.name || "image"}
                  >
                    image
                  </div>
                )
              )}
            </div>
          )}
          {message.content && (
            <div className="rounded-lg px-3 py-2 text-sm bg-primary text-primary-foreground whitespace-pre-wrap">
              {message.content}
            </div>
          )}
        </div>
      </div>
    );
  }

  // Parse renderables only after streaming is complete
  const { renderables, plainText: rawPlainText } = message.isStreaming
    ? { renderables: [], plainText: message.content }
    : parseRenderables(message.content);

  const plainText = useTypingEffect(rawPlainText, !!message.isStreaming);

  const suggestedActions = renderables.filter(
    (r): r is SuggestedActionsData => r.type === "suggested_actions"
  );
  const visualRenderables = renderables.filter(
    (r) => r.type !== "suggested_actions"
  );

  const isToolCallOnly = !plainText && !visualRenderables.length && !suggestedActions.length && (message.toolCalls?.length ?? 0) > 0;

  return (
    <div className="flex justify-start">
      <div className={`text-sm text-foreground ${isToolCallOnly ? "py-1" : "py-2"}`} style={{ maxWidth: "100%", minWidth: 0 }}>
        {/* Tool call timeline — always detailed (dev sample) */}
        {message.toolCalls && message.toolCalls.length > 0 && (
          <ToolCallTimeline toolCalls={message.toolCalls} detailed={true} />
        )}
        {/* Rendered blocks (tables, charts, images) */}
        {visualRenderables.map((r, i) => (
          <AgentResponseRenderer key={i} data={r} />
        ))}
        {/* Markdown-rendered text content */}
        {plainText ? (
          <div className={`chat-markdown prose prose-sm dark:prose-invert max-w-none${message.isStreaming ? " chat-streaming" : ""}`}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{plainText}</ReactMarkdown>
          </div>
        ) : (
          visualRenderables.length === 0 && !suggestedActions.length && !message.toolCalls?.length && " "
        )}
        {/* Action chips for drill-down suggestions */}
        {suggestedActions.length > 0 && (
          <div className="flex flex-wrap gap-2 mt-3 pt-3 border-t border-border/40">
            {suggestedActions.flatMap((sa) => sa.actions).map((action, i) => (
              <button
                key={i}
                type="button"
                disabled={!actionsEnabled}
                onClick={() => onActionClick?.(action)}
                className={`inline-flex items-center rounded-full border px-3 py-1 text-xs transition-colors ${
                  actionsEnabled
                    ? "border-border bg-muted text-foreground hover:bg-primary hover:text-primary-foreground cursor-pointer"
                    : "border-border/50 bg-muted/50 text-muted-foreground cursor-default"
                }`}
              >
                {action}
              </button>
            ))}
          </div>
        )}
        {showActions && !message.isStreaming && message.content && (
          <MessageActions message={message} />
        )}
      </div>
    </div>
  );
}

/* ── Thinking Indicator ─────────────────────────────────────── */

function ThinkingIndicator({ compact }: { compact?: boolean }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (compact) return;
    const t = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [compact]);

  if (compact) {
    return (
      <div className="py-1">
        <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const label =
    elapsed < 3
      ? "Thinking..."
      : elapsed < 8
        ? "Preparing query..."
        : `Still working... ${elapsed}s`;

  return (
    <div className="flex items-center gap-2 py-2 text-xs text-muted-foreground">
      <Loader2 className="h-3 w-3 animate-spin" />
      <span>{label}</span>
    </div>
  );
}

/* ── Message Actions (copy only — no feedback thumbs in this sample) ── */

function MessageActions({ message }: { message: ChatMessage }) {
  const [copied, setCopied] = useState(false);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    };
  }, []);

  const handleCopy = () => {
    navigator.clipboard.writeText(message.content).catch(() => {});
    setCopied(true);
    if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    copyTimerRef.current = setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex items-center gap-1 mt-1.5">
      <button
        type="button"
        onClick={handleCopy}
        className="h-6 w-6 inline-flex items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
        title="Copy"
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      </button>
    </div>
  );
}

/* ── Drag Handle ────────────────────────────────────────────── */

function DragHandle({ onResize, onReset }: { onResize: (width: number) => void; onReset: () => void }) {
  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      let rafId: number | null = null;

      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";

      const onMove = (ev: MouseEvent) => {
        if (rafId !== null) return;
        rafId = requestAnimationFrame(() => {
          const newWidth = window.innerWidth - ev.clientX;
          onResize(newWidth);
          rafId = null;
        });
      };

      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        if (rafId !== null) cancelAnimationFrame(rafId);
      };

      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [onResize]
  );

  return (
    <div
      className="absolute left-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-primary transition-colors z-10"
      onMouseDown={handleMouseDown}
      onDoubleClick={onReset}
    />
  );
}

/* ── Main Sidebar ───────────────────────────────────────────── */

export default function ChatSidebar() {
  const { isOpen, isPinned, width, setOpen, setPinned, setWidth } = useChatSidebarState();
  const [input, setInput] = useState("");
  const [view, setView] = useState<"chat" | "history">("chat");
  const [pendingImages, setPendingImages] = useState<ChatImage[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Model list — fetched directly from modelsUrl() (no auth/proxy needed)
  const [modelData, setModelData] = useState<{ models: string[]; default: string } | null>(null);
  useEffect(() => {
    fetch(modelsUrl())
      .then((r) => r.json())
      .then((d) => setModelData(d))
      .catch(() => setModelData(null));
  }, []);

  const [model, setModel] = useState("");
  useEffect(() => {
    setModel(localStorage.getItem("sample-chat-model") || "");
  }, []);

  // Default the picker once the list loads
  useEffect(() => {
    if (model) return;
    const fallback = modelData?.default || modelData?.models?.[0];
    if (fallback) setModel(fallback);
  }, [model, modelData?.default, modelData?.models]);

  // If a previously-picked model is no longer in the list, fall back to default
  useEffect(() => {
    const list = modelData?.models ?? [];
    if (model && list.length > 0 && !list.includes(model) && modelData?.default) {
      setModel(modelData.default);
      localStorage.setItem("sample-chat-model", modelData.default);
    }
  }, [model, modelData?.models, modelData?.default]);

  const handleModelChange = useCallback((v: string) => {
    setModel(v);
    localStorage.setItem("sample-chat-model", v);
  }, []);

  const models = modelData?.models ?? [];
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const lastUserMsgRef = useRef<HTMLDivElement>(null);
  const lastUserMsgIdRef = useRef<string | null>(null);

  const {
    conversations,
    activeConversationId,
    loadConversations,
    saveCurrentConversation,
    loadConversation,
    removeConversation,
    startNewConversation,
  } = useConversations();

  const { messages, isLoading, error, hasActiveToolCall, sendMessage, stop, clear, loadFromConversation, getThreadId } =
    useChat({
      onMessagesChange: saveCurrentConversation,
      model: model || undefined,
    });

  // Suppress unused-variable warnings for hooks still returned but not needed here
  void hasActiveToolCall;

  // Scroll: when a NEW user message appears, pin it to the top
  useEffect(() => {
    const lastUserMsg = [...messages].reverse().find((m) => m.role === "user");
    if (!lastUserMsg || lastUserMsg.id === lastUserMsgIdRef.current) return;
    lastUserMsgIdRef.current = lastUserMsg.id;

    requestAnimationFrame(() => {
      const container = scrollRef.current;
      const userEl = lastUserMsgRef.current;
      if (container && userEl) {
        const elRect = userEl.getBoundingClientRect();
        const containerRect = container.getBoundingClientRect();
        const scrollOffset = elRect.top - containerRect.top + container.scrollTop;
        container.scrollTo({ top: scrollOffset, behavior: "smooth" });
      }
    });
  }, [messages]);

  // Focus input when sidebar opens
  useEffect(() => {
    if (isOpen && view === "chat") inputRef.current?.focus();
  }, [isOpen, view]);

  const handleNewChat = useCallback(() => {
    if (messages.length > 0) {
      saveCurrentConversation(getThreadId(), messages);
    }
    clear();
    startNewConversation();
    setView("chat");
  }, [messages, saveCurrentConversation, getThreadId, clear, startNewConversation]);

  const handleToggleHistory = useCallback(() => {
    if (view === "history") {
      setView("chat");
    } else {
      loadConversations();
      setView("history");
    }
  }, [view, loadConversations]);

  const handleSelectConversation = useCallback(
    (id: string) => {
      const conv = loadConversation(id);
      if (conv) {
        loadFromConversation(conv.messages, conv.id);
        setView("chat");
      }
    },
    [loadConversation, loadFromConversation]
  );

  const addFiles = useCallback(async (files: FileList | File[] | null | undefined) => {
    if (!files) return;
    const imgs = Array.from(files).filter((f) => f.type.startsWith("image/"));
    if (imgs.length === 0) return;
    const loaded = (await Promise.all(imgs.map(fileToChatImage))).filter(
      (x): x is ChatImage => x !== null
    );
    if (loaded.length === 0) return;
    setPendingImages((prev) => {
      const rawBytes = (img: ChatImage) => (img.data.length * 3) / 4;
      let total = prev.reduce((s, i) => s + rawBytes(i), 0);
      const next = [...prev];
      for (const img of loaded) {
        if (next.length >= IMAGE_MAX_COUNT) break;
        if (total + rawBytes(img) > IMAGE_MAX_TOTAL_BYTES) continue;
        next.push(img);
        total += rawBytes(img);
      }
      return next;
    });
  }, []);

  const removePendingImage = useCallback((idx: number) => {
    setPendingImages((prev) => prev.filter((_, i) => i !== idx));
  }, []);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if ((!text && pendingImages.length === 0) || isLoading) return;
    setInput("");
    const imgs = pendingImages;
    setPendingImages([]);
    sendMessage(text, imgs.length > 0 ? imgs : undefined);
  }

  const handleReset = useCallback(() => setWidth(DEFAULT_WIDTH), [setWidth]);

  const [debugCopied, setDebugCopied] = useState(false);
  const handleCopyDebug = useCallback(() => {
    const lastRunId = [...messages].reverse().find((m) => m.runId)?.runId ?? null;
    const bundle = {
      threadId: getThreadId(),
      lastRunId,
      model: model || modelData?.default || null,
      pageUrl: typeof window !== "undefined" ? window.location.href : null,
      timestamp: new Date().toISOString(),
    };
    const text = JSON.stringify(bundle, null, 2);
    navigator.clipboard?.writeText(text).then(
      () => {
        setDebugCopied(true);
        setTimeout(() => setDebugCopied(false), 1500);
      },
      () => {},
    );
  }, [messages, getThreadId, model, modelData?.default]);

  return (
    <>
      {/* Toggle button — accent ribbon tab docked to bottom-right edge */}
      {!isOpen && (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="fixed bottom-12 right-0 z-50 flex flex-col items-center gap-1.5 rounded-l-md bg-primary px-1.5 py-2 text-primary-foreground shadow-md hover:bg-primary/90 transition-colors"
          aria-label="Open Chat Assistant"
        >
          <MessageSquare className="h-3.5 w-3.5" />
          <span className="text-[11px] font-medium leading-none [writing-mode:vertical-rl] rotate-180">Assistant</span>
        </button>
      )}

      {/* Sidebar panel */}
      <div
        className={`fixed inset-y-0 right-0 z-50 max-w-full flex flex-col bg-background border-l border-border shadow-2xl transition-transform duration-200 ${
          isOpen ? "translate-x-0" : "translate-x-full"
        }`}
        style={{ width }}
      >
        {/* Drag handle */}
        <DragHandle onResize={setWidth} onReset={handleReset} />

        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <div className="flex items-center gap-2.5 min-w-0">
            {/* Niteco wordmark (real asset; never faked) — yellow on dark */}
            <img src="/niteco-logo.svg" alt="Niteco" className="h-5 w-auto shrink-0" />
            <span className="text-sm font-semibold text-muted-foreground shrink-0">Assistant</span>
          </div>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              onClick={handleCopyDebug}
              title="Copy debug info (session id, model, page) for bug reports"
            >
              {debugCopied ? <Check className="h-3.5 w-3.5 text-green-600" /> : <Bug className="h-3.5 w-3.5" />}
            </Button>
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={handleNewChat} title="New chat">
              <Plus className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className={`h-7 w-7 ${view === "history" ? "bg-muted" : ""}`}
              onClick={handleToggleHistory}
              title="Chat history"
            >
              <History className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              onClick={() => setPinned(!isPinned)}
              title={isPinned ? "Unpin sidebar" : "Pin sidebar"}
            >
              {isPinned ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}
            </Button>
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setOpen(false)} title="Close">
              <X className="h-4 w-4" />
            </Button>
          </div>
        </div>

        {/* Content area — history list or chat messages */}
        {view === "history" ? (
          <div className="flex-1 overflow-y-auto px-4 py-3">
            <ConversationList
              conversations={conversations}
              activeId={activeConversationId}
              onSelect={handleSelectConversation}
              onDelete={removeConversation}
            />
          </div>
        ) : (
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-2 scrollbar-none">
            {messages.length === 0 && (
              <div className="flex flex-col items-center gap-3 mt-16 text-center">
                <img src="/niteco-logo.svg" alt="Niteco" className="h-8 w-auto" />
                <p className="text-xs text-muted-foreground">
                  Ask me anything — I can check the time and read images.
                </p>
              </div>
            )}
            {messages.map((msg, index) => {
              if (msg.role === "context") {
                return (
                  <div key={msg.id} className="text-center text-[10px] text-muted-foreground py-1">
                    {msg.content}
                  </div>
                );
              }
              if (msg.role === "reasoning") {
                return <ReasoningBubble key={msg.id} content={msg.content} streaming={!!msg.isStreaming} />;
              }
              const isLastAssistant =
                msg.role === "assistant" &&
                index === messages.findLastIndex((m) => m.role === "assistant");
              const isLastUser =
                msg.role === "user" &&
                index === messages.findLastIndex((m) => m.role === "user");
              return (
                <div key={msg.id} ref={isLastUser ? lastUserMsgRef : undefined}>
                  <MessageBubble
                    message={msg}
                    onActionClick={(action) => sendMessage(action)}
                    actionsEnabled={isLastAssistant && !isLoading}
                    showActions={isLastAssistant && !isLoading}
                  />
                </div>
              );
            })}
            {isLoading && (() => {
              const reasoningStreaming = messages.some((m) => m.role === "reasoning" && m.isStreaming);
              if (reasoningStreaming) return null;
              const answerStreaming = messages.some((m) => m.role === "assistant" && m.isStreaming && m.content);
              return <ThinkingIndicator compact={answerStreaming} />;
            })()}
            {error && (
              <div className="rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">
                {error}
              </div>
            )}
            {messages.length > 0 && <div className="min-h-[80vh] shrink-0" />}
          </div>
        )}

        {/* Composer — only shown in chat view */}
        <form
          onSubmit={handleSubmit}
          onDragOver={(e) => { e.preventDefault(); if (!isDragging) setIsDragging(true); }}
          onDragLeave={(e) => { e.preventDefault(); setIsDragging(false); }}
          onDrop={(e) => { e.preventDefault(); setIsDragging(false); void addFiles(e.dataTransfer?.files); }}
          className={`px-4 py-3 border-t border-border ${view === "history" ? "hidden" : ""}`}
        >
          <div className={`flex flex-col gap-1.5 rounded-xl border bg-background px-3 py-2 ring-offset-background focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-2 ${isDragging ? "border-primary ring-2 ring-primary/40" : "border-input"}`}>
          <input
            ref={fileInputRef}
            type="file"
            accept={IMAGE_ACCEPT}
            multiple
            className="hidden"
            onChange={(e) => { void addFiles(e.target.files); e.target.value = ""; }}
          />
          {pendingImages.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {pendingImages.map((img, i) => (
                <div key={i} className="relative h-14 w-14 shrink-0 overflow-hidden rounded-md border border-border bg-muted">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={imageSrc(img)} alt={img.name || "attachment"} className="h-full w-full object-cover" />
                  <button
                    type="button"
                    onClick={() => removePendingImage(i)}
                    className="absolute right-0 top-0 rounded-bl bg-background/80 p-0.5 text-muted-foreground hover:text-foreground"
                    title="Remove image"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
          )}
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              e.target.style.height = "auto";
              e.target.style.height = `${Math.min(e.target.scrollHeight, 120)}px`;
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSubmit(e as unknown as FormEvent);
              }
            }}
            onPaste={(e) => {
              const files = Array.from(e.clipboardData?.items || [])
                .filter((it) => it.kind === "file" && it.type.startsWith("image/"))
                .map((it) => it.getAsFile())
                .filter((f): f is File => f !== null);
              if (files.length > 0) { e.preventDefault(); void addFiles(files); }
            }}
            placeholder="Ask something..."
            className="w-full text-sm min-h-10 max-h-30 resize-none scrollbar-none bg-transparent border-0 p-0 placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-0 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={isLoading}
            rows={1}
          />
          <div className="flex items-center gap-1">
            <Select value={model} onValueChange={handleModelChange}>
              <SelectTrigger className="h-7 text-xs max-w-44 min-w-0 border-0 shadow-none px-1.5 gap-1 text-muted-foreground hover:text-foreground focus:ring-0" title="Model">
                <SelectValue placeholder="Model" />
              </SelectTrigger>
              <SelectContent>
                {models.length === 0 && model && <SelectItem value={model}>{model}</SelectItem>}
                {models.map((m) => (
                  <SelectItem key={m} value={m} className="text-xs font-mono">{m}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="flex-1" />
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className="h-8 w-8 shrink-0 text-muted-foreground hover:text-foreground"
              onClick={() => fileInputRef.current?.click()}
              disabled={isLoading || pendingImages.length >= IMAGE_MAX_COUNT}
              title="Attach image"
            >
              <ImagePlus className="h-4 w-4" />
            </Button>
            {isLoading ? (
              <Button type="button" size="icon" variant="ghost" className="h-8 w-8 shrink-0" onClick={stop} title="Stop">
                <Square className="h-4 w-4" />
              </Button>
            ) : (
              <Button type="submit" size="icon" className="h-8 w-8 shrink-0" disabled={!input.trim() && pendingImages.length === 0}>
                <Send className="h-4 w-4" />
              </Button>
            )}
          </div>
          </div>
        </form>
      </div>
    </>
  );
}
