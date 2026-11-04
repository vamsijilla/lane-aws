"use client";

import { EyeOff } from "lucide-react";
import type { ImgHTMLAttributes } from "react";

import { pageImageUrl } from "@/components/evidence/layout";
import { revealPhoto, useRevealedPhoto } from "@/lib/photo-reveal";
import { useSession } from "@/lib/session";

type Props = Omit<ImgHTMLAttributes<HTMLImageElement>, "src"> & {
  pageId: string;
  variant?: "full" | "thumb" | "original";
  /** A license photo: blurred until an underwriter reveals it. */
  personal?: boolean;
  alt: string;
};

/** A page image; a license photo is the blurred copy until revealed in this session. */
export function PageImage({ pageId, variant = "full", personal = false, alt, ...rest }: Props) {
  const revealed = useRevealedPhoto(pageId);
  return (
    // eslint-disable-next-line @next/next/no-img-element -- authenticated API image
    <img src={pageImageUrl(pageId, variant, personal && revealed)} alt={alt} {...rest} />
  );
}

/** Under a license photo: reveal it (underwriters), or why it is blurred. */
export function PhotoReveal({ pageId }: { pageId: string }) {
  const session = useSession();
  const revealed = useRevealedPhoto(pageId);
  if (revealed) {
    return (
      <p className="text-xs text-graphite">
        Shown for this session. The reveal is recorded in the audit trail.
      </p>
    );
  }
  return (
    <p className="flex flex-wrap items-center gap-2 text-sm text-graphite">
      <EyeOff size={14} aria-hidden="true" />
      <span>Blurred for privacy.</span>
      {session.can("reveal_pii") ? (
        <button
          type="button"
          onClick={() => revealPhoto(pageId)}
          className="font-semibold text-interstate hover:underline"
        >
          Reveal photo
        </button>
      ) : (
        <span>Only an underwriter can reveal it.</span>
      )}
    </p>
  );
}
