"use client";
import { createContext, useContext, useEffect, useState } from "react";
import { getToken, getRoomId, setToken, setRoomId, clearSession } from "./api";

type Ctx = { token: string | null; roomId: number | null; ready: boolean;
  signIn: (t: string, r: number) => void; signOut: () => void };
const SessionCtx = createContext<Ctx>(null as any);
export const useSession = () => useContext(SessionCtx);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [token, setTok] = useState<string | null>(null);
  const [roomId, setRid] = useState<number | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => { setTok(getToken()); setRid(getRoomId()); setReady(true); }, []);
  const signIn = (t: string, r: number) => { setToken(t); setRoomId(r); setTok(t); setRid(r); };
  const signOut = () => { clearSession(); setTok(null); setRid(null); };
  return <SessionCtx.Provider value={{ token, roomId, ready, signIn, signOut }}>{children}</SessionCtx.Provider>;
}
