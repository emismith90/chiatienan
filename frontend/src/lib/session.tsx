"use client";
import { createContext, useContext, useEffect, useState } from "react";
import { getToken, getRoomId, setToken, setRoomId, clearSession, getMe } from "./api";

type Ctx = { token: string | null; roomId: number | null; ready: boolean; memberId: number | null;
  signIn: (t: string, r: number) => void; signOut: () => void };
const SessionCtx = createContext<Ctx>(null as any);
export const useSession = () => useContext(SessionCtx);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [token, setTok] = useState<string | null>(null);
  const [roomId, setRid] = useState<number | null>(null);
  const [ready, setReady] = useState(false);
  const [memberId, setMemberId] = useState<number | null>(null);
  useEffect(() => { setTok(getToken()); setRid(getRoomId()); setReady(true); }, []);
  // Fetch the logged-in member's own id so callers (e.g. the optimistic
  // send echo in useRoom) can stamp outgoing messages with the real author.
  useEffect(() => {
    if (!token) {
      setMemberId(null);
      return;
    }
    let live = true;
    getMe()
      .then((me: any) => { if (live) setMemberId(me?.id ?? null); })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [token]);
  const signIn = (t: string, r: number) => { setToken(t); setRoomId(r); setTok(t); setRid(r); };
  const signOut = () => { clearSession(); setTok(null); setRid(null); setMemberId(null); };
  return <SessionCtx.Provider value={{ token, roomId, ready, memberId, signIn, signOut }}>{children}</SessionCtx.Provider>;
}
