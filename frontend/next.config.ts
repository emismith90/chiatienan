import type { NextConfig } from "next";
const nextConfig: NextConfig = {
  output: "standalone",
  turbopack: { root: __dirname },
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
