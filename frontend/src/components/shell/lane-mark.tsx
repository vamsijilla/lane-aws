// Lane's mark: a route shield holding the source box Lane draws around every value.
// Same drawing as app/icon.svg; the shield takes the text colour, the marks the paper.
export function LaneMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" className={className}>
      <path
        fill="currentColor"
        d="M5 5C9 6.5 12 6.5 16 4C20 6.5 23 6.5 27 5C29.5 12 29 21 16 29C3 21 2.5 12 5 5Z"
      />
      <path
        d="M10 13V10h3M19 10h3v3M22 18v3h-3M13 21h-3v-3"
        fill="none"
        stroke="var(--paper)"
        strokeWidth="1.8"
      />
      <rect x="14.8" y="13" width="2.4" height="5" fill="var(--paper)" />
    </svg>
  );
}
