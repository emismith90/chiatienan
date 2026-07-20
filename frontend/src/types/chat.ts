/** An image attachment on a user turn. `data` is raw base64 (no data: prefix);
 * derive a preview URL as `data:${mimeType};base64,${data}`. `data` may be ""
 * if the raw bytes were stripped upstream — render a fallback then. */
export interface ChatImage {
  data: string;
  mimeType: string;
  name?: string;
}

export interface ToolCallEntry {
  id: string;
  name: string;
  args: string;
  result: string | null;
  status: "running" | "completed" | "error";
  startedAt: number;
  endedAt: number | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "context" | "reasoning";
  content: string;
  toolCalls?: ToolCallEntry[];
  isStreaming?: boolean;
  runId?: string;
  feedback?: string | null;
  /** Image attachments (user turns only). */
  images?: ChatImage[];
}
