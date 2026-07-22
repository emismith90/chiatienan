"use client";
import { createContext, useContext, useEffect, useState } from "react";
import { getInvite, getMe, roomInfo } from "./api";
import {
  StoredRoom, listRooms, migrateLegacy, renameRoom,
  removeRoom as storeRemoveRoom, touchRoom, upsertRoom,
} from "./rooms-store";

type Ctx = {
  token: string | null; roomId: number | null; roomName: string;
  rooms: StoredRoom[]; ready: boolean; memberId: number | null;
  signIn: (token: string, roomId: number, roomName: string) => void;
  signOut: () => void;
  switchRoom: (roomId: number) => void;
  removeRoom: (roomId: number) => void;
};
const SessionCtx = createContext<Ctx>(null as any);
export const useSession = () => useContext(SessionCtx);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [rooms, setRooms] = useState<StoredRoom[]>([]);
  const [ready, setReady] = useState(false);
  const [memberId, setMemberId] = useState<number | null>(null);

  const refresh = () => setRooms(listRooms());

  useEffect(() => { migrateLegacy(); refresh(); setReady(true); }, []);

  const active = rooms[0] ?? null;
  const token = active?.token ?? null;
  const roomId = active?.roomId ?? null;

  // Legacy-migrated entries carry no name — resolve the active one lazily:
  // invite token (authed, active room) → public room info → name.
  useEffect(() => {
    if (!active || active.roomName) return;
    let live = true;
    getInvite(active.roomId)
      .then(({ invite_token }) => roomInfo(invite_token))
      .then((info: any) => {
        if (!live || !info?.name) return;
        renameRoom(active.roomId, info.name);
        refresh();
      })
      .catch(() => {});
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active?.roomId, active?.roomName]);

  // Fetch the logged-in member's own id so callers (e.g. the optimistic
  // send echo in useRoom) can stamp outgoing messages with the real author.
  useEffect(() => {
    if (!token) { setMemberId(null); return; }
    let live = true;
    getMe().then((me: any) => { if (live) setMemberId(me?.id ?? null); }).catch(() => {});
    return () => { live = false; };
  }, [token]);

  const signIn = (t: string, r: number, name: string) => {
    upsertRoom({ roomId: r, roomName: name, token: t });
    refresh();
  };
  const switchRoom = (r: number) => { touchRoom(r); refresh(); };
  const removeRoom = (r: number) => { storeRemoveRoom(r); refresh(); };
  // "Sign out" of the active room = drop it from this device; the next
  // most-recent room (if any) takes over. Server-side member is untouched.
  const signOut = () => {
    if (active) storeRemoveRoom(active.roomId);
    setMemberId(null);
    refresh();
  };

  return (
    <SessionCtx.Provider
      value={{ token, roomId, roomName: active?.roomName ?? "", rooms, ready,
               memberId, signIn, signOut, switchRoom, removeRoom }}
    >
      {children}
    </SessionCtx.Provider>
  );
}
