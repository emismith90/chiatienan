"use client";
import { useCallback, useEffect, useState } from "react";
import * as api from "@/lib/api";
import type { KnowledgeData } from "@/lib/api";

/** Fetches what Phoenix knows: places, notes, and the conversation memory.
 *
 * Mirrors `use-ledger`: refetches whenever `version` changes (bumped by the room
 * stream's `knowledge:changed` event), so the panel stays live without its own SSE
 * connection. `reload` exists for the write path — every editor calls it after a
 * save, and on a 409 it is the whole recovery.
 *
 * `enabled` gates the fetch so a room that never opens the tab never pays for the
 * payload. Data already fetched is kept when it flips back to false, so the tab
 * strip does not re-flash a spinner — but re-opening the tab *does* refetch, which
 * is the cheap self-heal for an SSE connection that dropped while it was hidden.
 */
export function useKnowledge(roomId: number, version: number, enabled = true) {
  const [data, setData] = useState<KnowledgeData | null>(null);
  const [loading, setLoading] = useState(false);
  const [bump, setBump] = useState(0);

  const reload = useCallback(() => setBump((b) => b + 1), []);

  useEffect(() => {
    if (!enabled) return;
    let live = true;
    setLoading(true);
    api
      .getKnowledge(roomId)
      .then((d) => live && setData(d))
      .catch(() => {})
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [roomId, version, bump, enabled]);

  return { data, loading, reload };
}
