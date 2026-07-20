"use client";

import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { StoredConversation } from "@/lib/chat-storage";

function formatRelativeTime(ts: number): string {
  const diff = Date.now() - ts;
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(ts).toLocaleDateString();
}

interface ConversationListProps {
  conversations: StoredConversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}

export function ConversationList({ conversations, activeId, onSelect, onDelete }: ConversationListProps) {
  if (conversations.length === 0) {
    return (
      <p className="text-center text-xs text-muted-foreground mt-8">
        No conversations yet
      </p>
    );
  }

  return (
    <div className="space-y-0.5">
      {conversations.map((conv) => (
        <div
          key={conv.id}
          className={`group flex items-center gap-2 px-3 py-2 rounded-md cursor-pointer hover:bg-muted transition-colors ${
            conv.id === activeId ? "bg-muted" : ""
          }`}
          onClick={() => onSelect(conv.id)}
        >
          <div className="flex-1 min-w-0">
            <div className="text-sm truncate">{conv.title || "Untitled"}</div>
            <div className="text-[10px] text-muted-foreground">
              {conv.messages.length} message{conv.messages.length !== 1 ? "s" : ""} · {formatRelativeTime(conv.updatedAt)}
            </div>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity"
            onClick={(e) => {
              e.stopPropagation();
              onDelete(conv.id);
            }}
            title="Delete conversation"
          >
            <Trash2 className="h-3 w-3" />
          </Button>
        </div>
      ))}
    </div>
  );
}
