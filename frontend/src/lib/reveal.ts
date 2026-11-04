/**
 * Scroll to the element a link's #hash names once it has rendered (data loads after the route),
 * outline it briefly, and select it if it is a selectable card (data-hash-select).
 */
export function revealHash(hash: string) {
  const id = decodeURIComponent(hash.replace(/^#/, ""));
  if (!id) return;
  const started = performance.now();
  const tick = () => {
    const el = document.getElementById(id);
    if (!el) {
      if (performance.now() - started < 4000) window.setTimeout(tick, 60);
      return;
    }
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    if (el.dataset.hashSelect !== undefined) el.click();
    el.setAttribute("data-flash", "");
    window.setTimeout(() => el.removeAttribute("data-flash"), 1600);
  };
  tick();
}
