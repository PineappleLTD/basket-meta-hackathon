// Width of the baskets sidebar, stored per browser. The shell's grid reads it from --sidebar-w.

export const SIDEBAR_DEFAULT = 280;
export const SIDEBAR_MIN = 200;
export const SIDEBAR_MAX = 720;

// Room left for the page beside the sidebar. .shell-body in globals.css holds the same limit as the window shrinks.
const MAIN_MIN = 480;

const SIDEBAR_KEY = "basket.sidebar";

// Runs in <head> before first paint, so the sidebar never jumps from the default to the saved width.
export const SIDEBAR_SCRIPT = `(function(){try{var w=parseInt(localStorage.getItem("${SIDEBAR_KEY}"),10);if(w>0)document.documentElement.style.setProperty("--sidebar-w",w+"px")}catch(e){}})()`;

// Sets the width within its limits and returns the width it settled on.
export function setSidebarWidth(width: number): number {
  const next = Math.round(Math.max(SIDEBAR_MIN, Math.min(width, SIDEBAR_MAX, window.innerWidth - MAIN_MIN)));
  document.documentElement.style.setProperty("--sidebar-w", `${next}px`);
  try {
    localStorage.setItem(SIDEBAR_KEY, String(next));
  } catch {}
  return next;
}
