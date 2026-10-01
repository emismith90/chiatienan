/** A sun — "sol" — for the GPT-6.1 Sol engine. A neutral glyph drawn here, not
 * OpenAI's artwork: swap in the official mark if one is licensed for this use.
 *
 * Inlined rather than served from `public/`: its first job is the boot splash,
 * which paints before anything has been fetched, and a logo that pops in a
 * beat late is worse than no logo. `fill` accepts `currentColor` where the
 * surrounding text should own the colour.
 */
export function SolMark({
  className = "",
  fill = "#F5A524",
  width,
  height,
}: {
  className?: string;
  fill?: string;
  width?: number;
  height?: number;
}) {
  const rays = Array.from({ length: 8 }, (_, i) => i * 45);
  return (
    <svg
      viewBox="0 0 24 24"
      role="img"
      aria-label="Sol"
      className={className}
      fill={fill}
      width={width}
      height={height}
      xmlns="http://www.w3.org/2000/svg"
    >
      <circle cx="12" cy="12" r="5" />
      {rays.map((deg) => (
        <rect key={deg} x="11" y="1" width="2" height="4" rx="1" transform={`rotate(${deg} 12 12)`} />
      ))}
    </svg>
  );
}
