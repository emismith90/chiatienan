import "./globals.css";
import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { Inter, JetBrains_Mono } from "next/font/google";
import { SessionProvider } from "@/lib/session";
import { ServiceWorkerRegister } from "@/lib/sw-register";

const inter = Inter({ subsets: ["latin", "vietnamese"], variable: "--font-inter" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jetbrains" });

export const metadata: Metadata = {
  // The app keeps its name — only the bot is Phoenix. "Reborn:" marks the
  // new-engine relaunch; the home-screen label (short_name / apple title)
  // stays the plain "chiatienan" people already have.
  title: "Reborn: chiatienan",
  applicationName: "chiatienan",
  manifest: "/manifest.webmanifest",
  // iOS doesn't read the web manifest for "Add to Home Screen"; these give it a
  // standalone launch, a titled icon, and a crisp home-screen glyph.
  appleWebApp: { capable: true, title: "chiatienan", statusBarStyle: "default" },
  // The `?v=` is load-bearing, not decoration: the icon FILES change while their
  // URLs don't, and a browser favicon store, a service-worker cache and an
  // installed Android WebAPK all key on the URL. The Phoenix rebrand shipped new
  // bytes at the old paths and every installed client kept the old picture.
  // Bump this with ICON_VERSION in scripts/gen-icons.py whenever the art moves.
  // (src/app/favicon.ico is picked up by Next on its own and needs no entry.)
  icons: {
    icon: "/icon-192.png?v=2",
    apple: [{ url: "/apple-touch-icon.png?v=2", sizes: "180x180" }],
  },
};

export const viewport: Viewport = {
  themeColor: "#C0472E",
  width: "device-width",
  initialScale: 1,
  // Content extends under the notch / home indicator; components opt back in
  // to the safe area with the .pt-safe / .pb-safe utilities.
  viewportFit: "cover",
  // Let the layout viewport shrink when the on-screen keyboard opens, so the
  // composer stays visible above it instead of being covered.
  interactiveWidget: "resizes-content",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="vi" className={`${inter.variable} ${mono.variable}`} suppressHydrationWarning>
      <body>
        <SessionProvider>{children}</SessionProvider>
        <ServiceWorkerRegister />
      </body>
    </html>
  );
}
