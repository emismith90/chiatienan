import type { ChatImage } from "@/types/chat";

/** A message the user composed that hasn't been accepted by the server yet.
 * Persisted so it survives an offline reload and sends when connectivity
 * returns. `id` is a client token (not the server message id). */
export interface OutboxRecord {
  id: string;
  roomId: number;
  body: string;
  images?: ChatImage[];
  createdAt: number;
}

/** Persistence seam so the flush/queue logic can be unit-tested against an
 * in-memory store while production uses IndexedDB. */
export interface OutboxStore {
  list(roomId: number): Promise<OutboxRecord[]>;
  add(rec: OutboxRecord): Promise<void>;
  remove(id: string): Promise<void>;
}

/** In-memory store — used by tests, and the fallback when IndexedDB is
 * unavailable (private-mode Safari, etc.) so sends never throw. */
export class MemoryOutboxStore implements OutboxStore {
  private recs = new Map<string, OutboxRecord>();
  async list(roomId: number): Promise<OutboxRecord[]> {
    return [...this.recs.values()]
      .filter((r) => r.roomId === roomId)
      .sort((a, b) => a.createdAt - b.createdAt);
  }
  async add(rec: OutboxRecord): Promise<void> {
    this.recs.set(rec.id, rec);
  }
  async remove(id: string): Promise<void> {
    this.recs.delete(id);
  }
}

const DB_NAME = "chiatienan";
const STORE = "outbox";

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const os = db.createObjectStore(STORE, { keyPath: "id" });
        os.createIndex("roomId", "roomId", { unique: false });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

class IdbOutboxStore implements OutboxStore {
  async list(roomId: number): Promise<OutboxRecord[]> {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, "readonly");
      const idx = tx.objectStore(STORE).index("roomId");
      const req = idx.getAll(roomId);
      req.onsuccess = () =>
        resolve((req.result as OutboxRecord[]).sort((a, b) => a.createdAt - b.createdAt));
      req.onerror = () => reject(req.error);
    });
  }
  async add(rec: OutboxRecord): Promise<void> {
    const db = await openDb();
    await tx(db, "readwrite", (os) => os.put(rec));
  }
  async remove(id: string): Promise<void> {
    const db = await openDb();
    await tx(db, "readwrite", (os) => os.delete(id));
  }
}

function tx(db: IDBDatabase, mode: IDBTransactionMode, op: (os: IDBObjectStore) => IDBRequest): Promise<void> {
  return new Promise((resolve, reject) => {
    const t = db.transaction(STORE, mode);
    op(t.objectStore(STORE));
    t.oncomplete = () => resolve();
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error);
  });
}

/** Production store: IndexedDB, or an in-memory fallback when it's missing. */
export function defaultStore(): OutboxStore {
  if (typeof indexedDB === "undefined") return new MemoryOutboxStore();
  try {
    return new IdbOutboxStore();
  } catch {
    return new MemoryOutboxStore();
  }
}

export function newRecord(roomId: number, body: string, images: ChatImage[] | undefined, now: number): OutboxRecord {
  const rand = Math.random().toString(36).slice(2, 8);
  return { id: `o-${now}-${rand}`, roomId, body, images: images?.length ? images : undefined, createdAt: now };
}

export interface FlushResult {
  sent: string[];
  remaining: number;
  stoppedByError: boolean;
}

/**
 * Drain a room's outbox in send order. Each record is posted via `post`; on
 * success it's removed and its id is collected. The drain **stops at the first
 * failure** (assumed still-offline / transient) and leaves that record and all
 * later ones queued — preserving order and avoiding a hot retry loop. Safe to
 * call repeatedly (on the `online` event, on room mount).
 */
export async function flushOutbox(
  store: OutboxStore,
  roomId: number,
  post: (rec: OutboxRecord) => Promise<void>,
): Promise<FlushResult> {
  const pending = await store.list(roomId);
  const sent: string[] = [];
  for (const rec of pending) {
    try {
      await post(rec);
    } catch {
      return { sent, remaining: pending.length - sent.length, stoppedByError: true };
    }
    await store.remove(rec.id);
    sent.push(rec.id);
  }
  return { sent, remaining: 0, stoppedByError: false };
}
