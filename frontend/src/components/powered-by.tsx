import { DeepSeekMark } from "./deepseek-mark";

/** The engine the room is actually talking to.
 *
 * Kept as a constant rather than fetched: this renders on the boot splash,
 * before there is a session or a reachable API. It must track `pi_model` in
 * `backend/app/config.py` (`PI_MODEL`, default `~deepseek/deepseek-v4-flash-latest`)
 * — if that default moves, move this with it.
 */
export const ENGINE_NAME = "DeepSeek V4 Flash";

/** chiatienan × DeepSeek, as a lockup: the app's own icon, a multiplication
 * sign, the whale. The `?v=2` matches the cache-busting suffix `layout.tsx`
 * uses on the same file — without it this would be a second URL for the same
 * bytes, and the service worker would cache the icon twice.
 */
function Lockup({ px }: { px: number }) {
  return (
    <span className="inline-flex items-center gap-2.5" aria-hidden>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/icon-192.png?v=2" alt="" width={px} height={px} className="rounded-[22%]" />
      <span className="text-[var(--text-secondary)]" style={{ fontSize: px * 0.5 }}>
        ×
      </span>
      <DeepSeekMark className="shrink-0" fill="#4D6BFE" width={px} height={px} />
    </span>
  );
}

/** Engine attribution. `variant="splash"` is the boot screen — icons above the
 * wordmark, centred; `variant="line"` is the one-line footer form. */
export function PoweredBy({ variant = "line" }: { variant?: "splash" | "line" }) {
  if (variant === "splash") {
    return (
      <div className="flex flex-col items-center gap-4">
        <Lockup px={44} />
        <p className="text-xs text-[var(--text-secondary)]">Powered by {ENGINE_NAME}</p>
      </div>
    );
  }
  return (
    <p className="inline-flex flex-wrap items-center justify-center gap-x-2 gap-y-1 text-xs text-[var(--text-secondary)]">
      <Lockup px={18} />
      <span>Powered by {ENGINE_NAME}</span>
    </p>
  );
}
