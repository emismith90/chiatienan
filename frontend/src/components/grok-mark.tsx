/** A ring cut by a slash, for the Grok engine. A neutral glyph drawn here, not
 * xAI's artwork: swap in the official mark if one is licensed for this use.
 *
 * Inlined rather than served from `public/`: its first job is the boot splash,
 * which paints before anything has been fetched, and a logo that pops in a
 * beat late is worse than no logo. `fill` accepts `currentColor` where the
 * surrounding text should own the colour.
 */
export function GrokMark({
  className = "",
  fill = "currentColor",
  width,
  height,
}: {
  className?: string;
  fill?: string;
  width?: number;
  height?: number;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      role="img"
      aria-label="Grok"
      className={className}
      fill="none"
      stroke={fill}
      strokeWidth={2.2}
      strokeLinecap="round"
      width={width}
      height={height}
      xmlns="http://www.w3.org/2000/svg"
    >
      <circle cx="12" cy="12" r="7.5" />
      <path d="M4 20 20 4" />
    </svg>
  );
}
