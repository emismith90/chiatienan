"use client";
import { useEffect, useState } from "react";
import * as api from "@/lib/api";
import type { LedgerData } from "@/lib/api";

/** Fetches the room ledger; refetches whenever `version` changes (bumped by the
 * room stream's `ledger:changed` event) so the panel stays live without its own
 * SSE connection. */
export function useLedger(roomId: number, version: number) {
  const [data, setData] = useState<LedgerData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .getLedger(roomId)
      .then((d) => live && setData(d))
      .catch(() => {})
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [roomId, version]);

  return { data, loading };
}
