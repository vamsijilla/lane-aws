"use client";

import clsx from "clsx";
import { Maximize2, ZoomIn } from "lucide-react";
import { useLayoutEffect, useRef, useState } from "react";

import { PageImage } from "@/components/evidence/page-image";

const MAX_HEIGHT = 520;

/**
 * Callers key this by page and fact, so a new source starts zoomed in with a fresh image.
 * A rendered page with the source box outlined. The camera eases to the box (about 280 ms) and
 * the outline draws once; "Whole page" zooms back out. Boxes are in page points.
 */
export function PageView({
  pageId,
  width,
  height,
  boxes,
  label,
  personal = false,
}: {
  pageId: string;
  width: number;
  height: number;
  boxes: number[][];
  label: string;
  /** A license photo: blurred until an underwriter reveals it. */
  personal?: boolean;
}) {
  const frame = useRef<HTMLDivElement>(null);
  const [cw, setCw] = useState(0);
  const [fit, setFit] = useState(boxes.length === 0);
  const [loaded, setLoaded] = useState(false);

  useLayoutEffect(() => {
    const el = frame.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setCw(entry.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const aspect = height / width;
  const frameH = Math.min(cw * aspect, MAX_HEIGHT);
  const union = boxes.length
    ? [
        Math.min(...boxes.map((b) => b[0])),
        Math.min(...boxes.map((b) => b[1])),
        Math.max(...boxes.map((b) => b[2])),
        Math.max(...boxes.map((b) => b[3])),
      ]
    : null;

  let zoom = 1;
  let tx = 0;
  let ty = 0;
  if (!fit && union && cw) {
    const bw = Math.max((union[2] - union[0]) / width, 0.02);
    const bh = Math.max((union[3] - union[1]) / height, 0.01);
    zoom = Math.min(3.2, Math.max(1.25, Math.min(0.5 / bw, (0.35 * frameH) / (bh * cw * aspect))));
    const imgW = cw * zoom;
    const imgH = imgW * aspect;
    const cx = ((union[0] + union[2]) / 2 / width) * imgW;
    const cy = ((union[1] + union[3]) / 2 / height) * imgH;
    tx = Math.min(0, Math.max(cw - imgW, cw / 2 - cx));
    ty =
      imgH <= frameH ? (frameH - imgH) / 2 : Math.min(0, Math.max(frameH - imgH, frameH / 2 - cy));
  } else if (cw) {
    const imgH = cw * aspect;
    ty = imgH <= frameH ? 0 : 0;
    zoom = Math.min(1, frameH / imgH);
    tx = (cw - cw * zoom) / 2;
  }

  return (
    <div className="space-y-2">
      <div
        ref={frame}
        className="relative overflow-hidden rounded-[var(--radius-cell)] border border-hairline bg-surface-sunk"
        style={{ height: frameH || 320 }}
      >
        {cw ? (
          <div
            className="absolute top-0 left-0 origin-top-left transition-transform duration-[280ms] ease-out motion-reduce:transition-none"
            style={{
              width: cw,
              height: cw * aspect,
              transform: `translate(${tx}px, ${ty}px) scale(${zoom})`,
            }}
          >
            <PageImage
              pageId={pageId}
              personal={personal}
              alt={label}
              onLoad={() => setLoaded(true)}
              className={clsx(
                "block h-full w-full bg-white transition-opacity duration-200",
                loaded ? "opacity-100" : "opacity-0",
              )}
            />
            <svg
              viewBox={`0 0 ${width} ${height}`}
              className="pointer-events-none absolute inset-0 h-full w-full"
              aria-hidden="true"
            >
              {boxes.map((b, i) => {
                const pad = 2.5;
                const x = b[0] - pad;
                const y = b[1] - pad;
                const w = b[2] - b[0] + pad * 2;
                const h = b[3] - b[1] + pad * 2;
                const perimeter = 2 * (w + h);
                return (
                  <g key={`${pageId}-${i}-${b.join(",")}`}>
                    <rect x={x} y={y} width={w} height={h} fill="var(--interstate)" opacity={0.1} />
                    <rect
                      x={x}
                      y={y}
                      width={w}
                      height={h}
                      fill="none"
                      stroke="var(--interstate)"
                      strokeWidth={Math.max(1.2, width / 500)}
                      rx={1.5}
                      className="lens-draw"
                      style={{ strokeDasharray: perimeter, strokeDashoffset: perimeter }}
                    />
                  </g>
                );
              })}
            </svg>
          </div>
        ) : null}
        {!loaded ? (
          <div className="absolute inset-0 animate-pulse bg-surface-sunk" aria-hidden="true" />
        ) : null}
      </div>
      {boxes.length ? (
        <div className="flex justify-end">
          <button
            type="button"
            onClick={() => setFit((f) => !f)}
            className="inline-flex items-center gap-1 text-sm text-interstate hover:underline"
          >
            {fit ? (
              <ZoomIn size={14} aria-hidden="true" />
            ) : (
              <Maximize2 size={14} aria-hidden="true" />
            )}
            {fit ? "Zoom to the value" : "Whole page"}
          </button>
        </div>
      ) : null}
    </div>
  );
}
