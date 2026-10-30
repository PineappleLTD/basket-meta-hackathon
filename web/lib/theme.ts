// Light or dark, stored per browser. Until the user picks one, the system setting decides.

export type Theme = "light" | "dark";

const THEME_KEY = "basket.theme";

// Runs in <head> before first paint, so the page never flashes the wrong theme.
export const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem("${THEME_KEY}")}catch(e){}if(t!=="light"&&t!=="dark")t=matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";document.documentElement.setAttribute("data-theme",t)})()`;

export const currentTheme = (): Theme =>
  document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";

export function setTheme(theme: Theme) {
  document.documentElement.setAttribute("data-theme", theme);
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {}
}
