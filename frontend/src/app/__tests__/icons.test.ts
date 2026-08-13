/**
 * The icon files exist, and everything that points at them agrees on the
 * version query.
 *
 * WHY — the Phoenix rebrand shipped new art at the SAME urls (/icon-192.png,
 * /icon-512.png) and nobody saw it: browsers key their favicon store, the
 * service-worker cache and an installed Android WebAPK on the URL, so identical
 * urls read as "nothing changed". /favicon.ico did not exist at all and 404'd in
 * production. These assertions fail the next time art is replaced without the
 * `?v=` bump that actually delivers it.
 */
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..", "..", "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf8");

/** The `?v=N` every icon reference must carry, taken from the manifest. */
const manifest = JSON.parse(read("public/manifest.webmanifest"));
const VERSION = new URL(manifest.icons[0].src, "https://x").searchParams.get("v");

describe("app icons", () => {
  it("ships every file the manifest and the head reference", () => {
    for (const p of [
      "public/icon-192.png",
      "public/icon-512.png",
      "public/apple-touch-icon.png",
      "src/app/favicon.ico", // Next serves this at /favicon.ico
    ]) {
      expect(existsSync(join(ROOT, p)), `${p} is missing`).toBe(true);
    }
  });

  it("versions every manifest icon so installed PWAs re-fetch the art", () => {
    expect(VERSION).toBeTruthy();
    for (const icon of manifest.icons) {
      expect(icon.src, `${icon.src} is unversioned`).toContain(`?v=${VERSION}`);
    }
  });

  it("uses that same version in the document head and the service worker", () => {
    const layout = read("src/app/layout.tsx");
    expect(layout).toContain(`/icon-192.png?v=${VERSION}`);
    expect(layout).toContain(`/apple-touch-icon.png?v=${VERSION}`);

    const sw = read("public/sw.js");
    for (const line of sw.split("\n").filter((l) => l.includes("const SHELL"))) {
      for (const icon of line.match(/\/icon-\d+\.png[^"]*/g) ?? []) {
        expect(icon, `sw.js precaches ${icon}`).toContain(`?v=${VERSION}`);
      }
    }
  });

  it("keeps the service worker off cache-first for assets whose url is stable", () => {
    // Icons and the manifest change bytes without changing urls, so cache-first
    // pins them forever; only the build's content-hashed output may use it.
    const sw = read("public/sw.js");
    const immutable = sw.match(/function isImmutableAsset[\s\S]*?\n}/)?.[0] ?? "";
    expect(immutable).toContain("/_next/static/");
    expect(immutable).not.toMatch(/\.png|manifest|\.ico/);
  });
});
