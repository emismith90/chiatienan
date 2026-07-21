import type { NextConfig } from "next";
const nextConfig: NextConfig = {
  output: "standalone",
  turbopack: { root: __dirname },
  // Local-dev only: disable Next's gzip so the dev rewrite-proxy streams SSE
  // (/api/rooms/*/stream) unbuffered. gzip buffers the response, so the
  // browser's reader gets nothing until the connection closes and live chat /
  // bot replies never render without a reload. In production Caddy is the only
  // router and does no compression (no `encode` in the Caddyfile), so the
  // deployed Next build keeps compressing its own assets normally.
  compress: process.env.NODE_ENV === "production",
  // Local-dev only: mirror the Caddy routing (/api/* and /internal/* -> backend).
  // In a production build (NODE_ENV=production) these rewrites are skipped, so
  // Caddy stays the single router in the deployed stack.
  async rewrites() {
    if (process.env.NODE_ENV === "production") return [];
    const backend = process.env.BACKEND_ORIGIN || "http://127.0.0.1:8000";
    return [
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
      { source: "/internal/:path*", destination: `${backend}/internal/:path*` },
    ];
  },
};
export default nextConfig;
