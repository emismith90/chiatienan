import "./globals.css";
import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { Inter, JetBrains_Mono } from "next/font/google";
import { SessionProvider } from "@/lib/session";
import { ServiceWorkerRegister } from "@/lib/sw-register";

const inter = Inter({ subsets: ["latin", "vietnamese"], variable: "--font-inter" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jetbrains" });

export const metadata: Metadata = {
  title: "chiatienan",
  applicationName: "chiatienan",
  manifest: "/manifest.webmanifest",
  // iOS doesn't read the web manifest for "Add to Home Screen"; these give it a
  // standalone launch, a titled icon, and a crisp home-screen glyph.
  appleWebApp: { capable: true, title: "chiatienan", statusBarStyle: "default" },
  icons: {
    icon: "/icon-192.png",
    apple: [{ url: "/icon-192.png", sizes: "180x180" }],
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
