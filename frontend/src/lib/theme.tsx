"use client";
import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

const STORAGE_KEY = "chiatienan.theme";

function getSystemTheme(): Theme {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme: Theme | null) {
  if (theme) {
    document.documentElement.dataset.theme = theme;
  } else {
    delete document.documentElement.dataset.theme;
  }
}

/**
 * Reads/writes the persisted theme choice and stamps `data-theme` on
 * `document.documentElement`. Default (no stored preference) follows the OS
 * via the `prefers-color-scheme` media query already wired in globals.css —
 * in that case `theme` reflects the current OS preference for UI purposes,
 * but no `data-theme` attribute is set until the user makes an explicit choice.
 */
export function useTheme() {
  const [theme, setThemeState] = useState<Theme>("light");

  useEffect(() => {
    const stored = typeof window !== "undefined" ? (localStorage.getItem(STORAGE_KEY) as Theme | null) : null;
    const initial = stored ?? getSystemTheme();
    setThemeState(initial);
    applyTheme(stored);
  }, []);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    localStorage.setItem(STORAGE_KEY, next);
    applyTheme(next);
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(theme === "dark" ? "light" : "dark");
  }, [theme, setTheme]);

  return { theme, setTheme, toggleTheme };
}

/** Small light/dark toggle button; mounted in the room header (Task 6). */
export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  return (
    <button
      type="button"
      onClick={toggleTheme}
      aria-label="Toggle theme"
      className="rounded-lg border-[var(--border)] border px-3 py-1.5 text-sm text-[var(--text-secondary)] shadow-sm transition-all duration-150 ease-in-out hover:bg-[var(--bg-base)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)]"
    >
      {theme === "dark" ? "Light mode" : "Dark mode"}
    </button>
  );
}
