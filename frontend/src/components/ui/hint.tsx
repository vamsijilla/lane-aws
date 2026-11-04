"use client";

import * as Tooltip from "@radix-ui/react-tooltip";
import clsx from "clsx";

/** One provider for the app: a short pause before a hint opens, none when moving between them. */
export function HintProvider({ children }: { children: React.ReactNode }) {
  return (
    <Tooltip.Provider delayDuration={350} skipDelayDuration={250}>
      {children}
    </Tooltip.Provider>
  );
}

/**
 * A hint explains what is already on screen: what a rule checks, what a status means. It opens
 * on hover and on keyboard focus and holds text only, never an action. What a person needs to
 * decide is always on the page; a hint adds the why (a rule's purpose, an assumption's wording).
 *
 * By default the hinted text is wrapped and made focusable, with a dotted underline so a reader
 * can tell it explains more. `asChild` hands the hint to a link or button that is already
 * focusable.
 */
export function Hint({
  content,
  children,
  asChild,
  side = "top",
  className,
}: {
  content: React.ReactNode;
  children: React.ReactNode;
  asChild?: boolean;
  side?: "top" | "bottom" | "left" | "right";
  className?: string;
}) {
  if (!content) return <>{children}</>;
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        {asChild ? (
          children
        ) : (
          <span
            tabIndex={0}
            className={clsx(
              "cursor-help rounded-[2px] underline decoration-hairline decoration-dotted underline-offset-4 focus-visible:outline-2",
              className,
            )}
          >
            {children}
          </span>
        )}
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          side={side}
          sideOffset={6}
          collisionPadding={12}
          className="hint z-50 max-w-[22rem] rounded-[var(--radius-control)] border border-hairline bg-surface px-3 py-2 text-sm leading-snug text-ink shadow-[var(--shadow-overlay)]"
        >
          {content}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}
