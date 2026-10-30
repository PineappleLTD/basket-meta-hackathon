"use client";

import { currentTheme, setTheme } from "@/lib/theme";

// Flips light/dark. Both icons are rendered and CSS shows the right one, so the server markup never mismatches.
export function ThemeToggle() {
  return (
    <button
      type="button"
      className="theme-toggle"
      aria-label="Toggle dark mode"
      title="Toggle dark mode"
      onClick={() => setTheme(currentTheme() === "dark" ? "light" : "dark")}
    >
      <svg viewBox="0 0 16 16" aria-hidden="true" className="theme-moon">
        <path d="M13.5 9.5A5.5 5.5 0 0 1 6.5 2.5a5.5 5.5 0 1 0 7 7Z" />
      </svg>
      <svg viewBox="0 0 16 16" aria-hidden="true" className="theme-sun">
        <circle cx="8" cy="8" r="3" />
        <path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1" />
      </svg>
    </button>
  );
}
