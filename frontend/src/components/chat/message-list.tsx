"use client";
import { BotMessage } from "./bot-message";
import { ExpenseDraftCard } from "./expense-draft-card";
import { PaymentDraftCard } from "./payment-draft-card";
import { AgentTimeline } from "./agent-timeline";
import type { TimelineStep } from "@/hooks/use-room";

interface Member {
  id: number;
  display_name: string;
  nickname?: string | null;
}

interface AttachmentImage {
  data: string;
  mimeType: string;
  name?: string;
}

interface Message {
  id: number | string;
  kind?: string;
  body: string;
  attachments?: any;
  created_at?: string | null;
  author?: { id: number | null; name?: string; nickname?: string | null } | null;
  pending?: boolean;
  queued?: boolean;
  error?: boolean;
}

function HumanMessage({ message }: { message: Message }) {
  const images: AttachmentImage[] = message.attachments?.images ?? [];
  // The optimistic pending bubble only carries the author id (no display
  // name yet) — label it "You" rather than falling through to the
  // "unknown author" copy until the real message reconciles it.
  const name = message.pending ? "You" : (message.author?.name ?? "Anonymous");
  return (
    <div className="flex flex-col items-end">
      <span className="mb-1 px-1 text-xs text-[var(--text-secondary)]">{name}</span>
      <div
        className={`max-w-[85%] rounded-lg border px-4 py-2.5 text-white shadow-sm transition-opacity duration-150 ${
          message.error ? "border-[var(--danger)]" : "border-[var(--border)]"
        } bg-[var(--accent-primary)] ${message.pending ? "opacity-60" : ""}`}
      >
        {message.error && (
          <p className="mb-1 text-xs font-medium text-white/90">Failed to send.</p>
        )}
        {message.queued && !message.error && (
          <p className="mb-1 flex items-center gap-1 text-xs font-medium text-white/80">
            <span aria-hidden>⏳</span> Chờ mạng để gửi…
          </p>
        )}
        {message.body && (
          <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">
            {message.body}
          </p>
        )}
        {images.length > 0 && (
          <div className="mt-2 grid grid-cols-2 gap-2">
            {images.map((img, i) =>
              img.data ? (
                <img
                  key={i}
                  src={`data:${img.mimeType};base64,${img.data}`}
                  alt={img.name || "attachment"}
                  className="max-h-48 w-full rounded-md border border-white/20 object-cover"
                />
              ) : (
                <div
                  key={i}
                  className="flex h-24 items-center justify-center rounded-md border border-white/20 bg-white/10 text-xs"
                >
                  🖼️ {img.name || "image"}
                </div>
              ),
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export function MessageList({
  messages,
  members,
  roomId,
  timelines,
}: {
  messages: Message[];
  members: Member[];
  roomId: number;
  /** Per-turn agent timelines (turn_id -> steps), keyed the same as
   * useRoom's `timelines`. A finished turn's timeline attaches, collapsed,
   * above the draft message it produced — see room-view.tsx for the
   * companion live-only rendering at the bottom of the thread. */
  timelines?: Record<string, TimelineStep[]>;
}) {
  return (
    <div className="flex flex-col gap-4">
      {messages.map((m) => {
        const turnId = (m.kind === "expense_draft" || m.kind === "payment_draft") ? m.attachments?.turn_id : undefined;
        const turnSteps = turnId ? timelines?.[turnId] : undefined;
        return m.kind === "context_reset" ? (
          <div key={m.id} className="flex justify-center py-1">
            <span className="rounded-full bg-[var(--surface-2,transparent)] px-3 py-1 text-center text-xs text-[var(--text-secondary)]">
              {m.body}
            </span>
          </div>
        ) : m.kind === "expense_draft" ? (
          <div key={m.id} className="flex flex-col items-start">
            <span className="mb-1 px-1 text-xs font-medium text-[var(--accent-text)]">
              Bot
            </span>
            {turnSteps && <AgentTimeline steps={turnSteps} live={false} />}
            <ExpenseDraftCard message={m} members={members} roomId={roomId} />
          </div>
        ) : m.kind === "payment_draft" ? (
          <div key={m.id} className="flex flex-col items-start">
            <span className="mb-1 px-1 text-xs font-medium text-[var(--accent-text)]">
              Bot
            </span>
            {turnSteps && <AgentTimeline steps={turnSteps} live={false} />}
            <PaymentDraftCard message={m} members={members} roomId={roomId} />
          </div>
        ) : m.kind === "bot" ? (
          <div key={m.id} className="flex flex-col items-start">
            <span className="mb-1 px-1 text-xs font-medium text-[var(--accent-text)]">
              Bot
            </span>
            <BotMessage body={m.body} attachments={m.attachments} roomId={roomId} />
          </div>
        ) : (
          <HumanMessage key={m.id} message={m} />
        );
      })}
    </div>
  );
}
