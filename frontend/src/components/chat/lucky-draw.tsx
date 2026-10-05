"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import * as api from "@/lib/api";

/** Names per second at full spin. */
const SPEED = 16;
/** Full-speed spin before the reel may start slowing down, so even an instant
 * server answer still looks like a draw. */
const MIN_SPIN_MS = 700;
/** Slow-down time, from full speed to a stop on the winner. */
const LAND_MS = 2600;

function reducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

const mod = (i: number, n: number) => ((i % n) + n) % n;
const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3);

/** The slot-machine reel: a column of names rolling through one window.
 *
 * Purely a show: it never chooses anyone. While `spinning` with no `winner` it
 * rolls at full speed (the server hasn't answered yet); once `winner` is known it
 * slows down and stops exactly on that name, then calls `onLanded`. Not spinning,
 * it simply shows `winner` (or `placeholder`). */
export function SlotReel({
  names,
  winner,
  spinning,
  onLanded,
  placeholder = "?",
  className = "",
}: {
  names: string[];
  winner: string | null;
  spinning: boolean;
  onLanded?: () => void;
  placeholder?: string;
  className?: string;
}) {
  // The winner is always on the reel, even if the list moved under it.
  const reel = useMemo(
    () => (winner != null && !names.includes(winner) ? [...names, winner] : names),
    [names, winner],
  );
  const target = winner != null ? reel.indexOf(winner) : -1;

  const [frame, setFrame] = useState({ pos: 0, fast: false });
  const live = useRef({ reel, target, onLanded });
  live.current = { reel, target, onLanded };

  // Reduced motion: no rolling — the winner shows the moment it is known.
  useEffect(() => {
    if (spinning && target >= 0 && reducedMotion()) live.current.onLanded?.();
  }, [spinning, target]);

  useEffect(() => {
    if (!spinning || reducedMotion()) return;
    let raf = 0;
    let pos = 0;
    let last: number | null = null;
    let started: number | null = null;
    let landing: { from: number; to: number; t0: number } | null = null;

    const tick = (now: number) => {
      if (last == null) last = started = now;
      const dt = (now - last) / 1000;
      last = now;
      const { reel: r, target: t } = live.current;
      if (!landing && t >= 0 && r.length > 0 && now - started! >= MIN_SPIN_MS) {
        // An ease-out starts at 3× its average speed, so this distance keeps the
        // hand-over from full spin seamless; then round up to land on the winner.
        const min = pos + (SPEED * LAND_MS) / 1000 / 3;
        let to = Math.ceil(min);
        to += mod(t - to, r.length);
        landing = { from: pos, to, t0: now };
      }
      if (landing) {
        const k = Math.min(1, (now - landing.t0) / LAND_MS);
        pos = landing.from + (landing.to - landing.from) * easeOutCubic(k);
        const speed = (3 * (landing.to - landing.from) * Math.pow(1 - k, 2)) / (LAND_MS / 1000);
        setFrame({ pos, fast: speed > 7 });
        if (k >= 1) {
          live.current.onLanded?.();
          return;
        }
      } else {
        pos += SPEED * dt;
        setFrame({ pos, fast: true });
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // Only (re)starting a spin restarts the loop; the winner arriving mid-spin
    // is read through `live`.
  }, [spinning]);

  const rolling = spinning && !reducedMotion();
  const n = reel.length;
  // Only the one or two names actually in the window are rendered.
  const items: { key: number; name: string; offset: number }[] = [];
  if (rolling && n > 0) {
    for (let i = Math.floor(frame.pos); i <= Math.ceil(frame.pos); i++) {
      items.push({ key: i, name: reel[mod(i, n)], offset: frame.pos - i });
    }
  } else {
    const still = target >= 0 ? reel[target] : spinning ? "…" : placeholder;
    items.push({ key: 0, name: still, offset: 0 });
  }

  return (
    <div aria-hidden className={`relative h-full w-full ${className}`}>
      {items.map((it) => (
        <div
          key={it.key}
          className={`absolute inset-0 flex items-center justify-center truncate px-3 font-extrabold ${
            rolling && frame.fast ? "blur-[1.5px]" : ""
          }`}
          style={{ transform: `translateY(${it.offset * 100}%)` }}
        >
          {it.name}
        </div>
      ))}
    </div>
  );
}

const CONFETTI_COLORS = ["#FFD43B", "#22D3EE", "#A3E635", "#F472B6", "#FFFFFF", "#818CF8"];

/** A burst of falling confetti over the stage. `fall` is how far a piece drops. */
export function Confetti({ count = 48, fall = "100vh" }: { count?: number; fall?: string }) {
  // A fixed pseudo-random spread (no Math.random), so it renders the same twice.
  const pieces = useMemo(
    () =>
      Array.from({ length: count }, (_, i) => {
        const r = (k: number) => mod(Math.sin(i * 12.9898 + k * 78.233) * 43758.5453, 1);
        return {
          left: `${r(1) * 100}%`,
          background: CONFETTI_COLORS[i % CONFETTI_COLORS.length],
          "--delay": `${r(2) * 0.6}s`,
          "--dur": `${1.8 + r(3) * 1.4}s`,
          "--dx": `${(r(4) - 0.5) * 160}px`,
          "--spin": `${(r(5) - 0.5) * 1440}deg`,
          "--fall": fall,
        } as React.CSSProperties;
      }),
    [count, fall],
  );
  return (
    <div aria-hidden className="lucky-confetti">
      {pieces.map((style, i) => (
        <i key={i} style={style} />
      ))}
    </div>
  );
}

interface DrawMember {
  id: number;
  display_name: string;
  default_participant?: boolean;
}

/** The Lucky Draw screen behind the header's 🎰 button: the room's saved draw
 * list as toggles, and a Draw button. The server picks the winner (same draw as
 * the bot's `pick_random`) and posts it to the room; the reel only lands on it. */
export function LuckyDrawDialog({
  roomId,
  members,
  onClose,
  onListChanged,
}: {
  roomId: number;
  members: DrawMember[];
  onClose: () => void;
  /** The list was saved — the parent refetches the roster that carries it. */
  onListChanged?: () => void;
}) {
  const [inDraw, setInDraw] = useState<Set<number>>(
    () => new Set(members.filter((m) => m.default_participant !== false).map((m) => m.id)),
  );
  const [spinning, setSpinning] = useState(false);
  const [names, setNames] = useState<string[]>([]);
  const [winner, setWinner] = useState<string | null>(null);
  const [landed, setLanded] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const drawRef = useRef<HTMLButtonElement>(null);

  const pool = members.filter((m) => inDraw.has(m.id));

  // The roster can arrive after the dialog opens, and is refetched after every
  // save — the saved list it carries is the truth.
  useEffect(() => {
    setInDraw(new Set(members.filter((m) => m.default_participant !== false).map((m) => m.id)));
  }, [members]);

  useEffect(() => {
    drawRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function toggle(id: number) {
    const before = inDraw;
    const next = new Set(inDraw);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setInDraw(next);
    setErr(null);
    try {
      await api.setDrawPool(roomId, [...next]);
      onListChanged?.();
    } catch (e: any) {
      setInDraw(before);
      setErr(e?.message || "Could not save the list");
    }
  }

  async function draw() {
    setErr(null);
    setLanded(false);
    setWinner(null);
    setNames(pool.map((m) => m.display_name));
    setSpinning(true);
    try {
      const res = await api.luckyDraw(roomId);
      setNames(res.candidates.map((c: { name: string }) => c.name));
      setWinner(res.chosen.name);
    } catch (e: any) {
      setSpinning(false);
      setErr(e?.message || "The draw failed, try again");
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Lucky Draw"
      className={`lucky-stage fixed inset-0 z-50 flex flex-col overflow-y-auto ${spinning ? "is-spinning" : ""}`}
    >
      {landed && <Confetti />}
      <div className="pt-safe flex justify-end px-4 pt-3">
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="rounded-full px-3 py-1 text-2xl leading-none text-white/90 hover:bg-white/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
        >
          ×
        </button>
      </div>

      <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col items-center gap-8 px-4 pb-8 pt-2 sm:justify-center">
        <h2 className="lucky-title text-5xl sm:text-7xl">Lucky Draw</h2>

        <div className={`lucky-frame w-full ${spinning ? "is-spinning" : ""}`}>
          <div className="lucky-window h-24 text-4xl sm:h-32 sm:text-6xl">
            <SlotReel
              names={names.length ? names : pool.map((m) => m.display_name)}
              winner={winner}
              spinning={spinning}
              onLanded={() => {
                setSpinning(false);
                setLanded(true);
              }}
            />
          </div>
        </div>

        <div role="status" aria-live="polite" className="min-h-6 text-center text-base font-semibold">
          {spinning ? "Drawing…" : landed && winner ? `🎉 ${winner}! Posted to the room.` : ""}
        </div>

        <button
          ref={drawRef}
          type="button"
          onClick={draw}
          disabled={spinning || pool.length === 0}
          className="lucky-button h-12 w-56 text-lg sm:w-72"
        >
          {spinning ? "Drawing…" : landed ? "Draw again" : "Draw"}
        </button>

        <section className="w-full rounded-xl bg-black/15 p-4">
          <h3 className="text-sm font-semibold">
            In the draw · {pool.length} of {members.length}
          </h3>
          <p className="mt-0.5 text-xs text-white/85">
            Tap a name to take it off or put it back. The list is saved for every later
            draw, and @phoenix uses it too.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {members.map((m) => {
              const on = inDraw.has(m.id);
              return (
                <button
                  key={m.id}
                  type="button"
                  aria-pressed={on}
                  disabled={spinning}
                  onClick={() => toggle(m.id)}
                  className={`min-h-9 rounded-full border px-3 py-1.5 text-sm font-medium transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white disabled:opacity-60 ${
                    on
                      ? "border-white bg-white text-[#C81E3A]"
                      : "border-white/60 bg-transparent text-white/80 line-through"
                  }`}
                >
                  {m.display_name}
                </button>
              );
            })}
          </div>
          {pool.length === 0 && (
            <p className="mt-2 text-xs font-semibold">Put at least one name back to draw.</p>
          )}
          {err && (
            <p role="alert" className="mt-2 text-sm font-semibold text-[#FFE08A]">
              {err}
            </p>
          )}
        </section>
      </div>
    </div>
  );
}

/** The Lucky Draw stage at chat-card size: the bot's "drawing…" placeholder and
 * the result card both use it, so a draw looks the same however it started. */
export function LuckyCard({
  names,
  winner,
  spinning,
  onLanded,
  confetti = false,
  children,
}: {
  names: string[];
  winner: string | null;
  spinning: boolean;
  onLanded?: () => void;
  confetti?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div className={`lucky-stage relative overflow-hidden rounded-lg px-4 pb-4 pt-3 text-center ${spinning ? "is-spinning" : ""}`}>
      {confetti && <Confetti count={28} fall="260px" />}
      <div className="lucky-title text-2xl">Lucky Draw</div>
      <div className={`lucky-frame lucky-frame--sm mt-3 ${spinning ? "is-spinning" : ""}`}>
        <div className="lucky-window h-14 text-2xl">
          <SlotReel names={names} winner={winner} spinning={spinning} onLanded={onLanded} />
        </div>
      </div>
      {children}
    </div>
  );
}
