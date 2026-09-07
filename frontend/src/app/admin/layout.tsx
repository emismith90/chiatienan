/** `/admin` is a real URL on a public host; keep it out of search indexes. */
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Agent OS — operator",
  robots: { index: false, follow: false },
};

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return children;
}
