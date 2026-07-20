export function parseSSE(buffer: string): { events: any[]; rest: string } {
  const parts = buffer.split("\n\n");
  const rest = parts.pop() ?? "";
  const events: any[] = [];
  for (const chunk of parts) {
    const line = chunk.split("\n").find((l) => l.startsWith("data:"));
    if (!line) continue;
    try { events.push(JSON.parse(line.slice(5).trim())); } catch { /* skip malformed */ }
  }
  return { events, rest };
}
