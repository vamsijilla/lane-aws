/**
 * Density: comfortable (the default) or compact, for people who work through many submissions.
 * Compact scales Tailwind's spacing unit, so every padding and gap tightens together.
 */
export type Density = "comfortable" | "compact";

export const DENSITY_STORAGE_KEY = "lane.density";

/** Runs in <head> before first paint, like the theme, so a compact layout never jumps. */
export const DENSITY_BOOTSTRAP = `(function(){try{if(localStorage.getItem("${DENSITY_STORAGE_KEY}")==="compact")document.documentElement.setAttribute("data-density","compact")}catch(e){}})()`;

export function readDensity(): Density {
  try {
    return localStorage.getItem(DENSITY_STORAGE_KEY) === "compact" ? "compact" : "comfortable";
  } catch {
    return "comfortable";
  }
}

export function applyDensity(density: Density): void {
  const root = document.documentElement;
  if (density === "compact") root.setAttribute("data-density", "compact");
  else root.removeAttribute("data-density");
  try {
    if (density === "compact") localStorage.setItem(DENSITY_STORAGE_KEY, "compact");
    else localStorage.removeItem(DENSITY_STORAGE_KEY);
  } catch {
    // Storage unavailable (private mode). The choice still applies for this page view.
  }
}
