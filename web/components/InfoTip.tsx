"use client";

import { useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

// Room kept between the tip and the window's edges, and between the tip and its "?".
const EDGE = 8;

// A "?" that explains the label beside it, shown on hover or keyboard focus. The tip is drawn
// over the page (not inside the scrolling column), above the "?" when there's room, and kept on
// screen. `end` lines it up with the "?"'s right edge, for labels near the right of the page.
export function InfoTip({ children, end }: { children: ReactNode; end?: boolean }) {
  const id = useId();
  const button = useRef<HTMLButtonElement>(null);
  const tip = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

  useLayoutEffect(() => {
    if (!open || !button.current || !tip.current) return setPos(null);
    const b = button.current.getBoundingClientRect();
    const t = tip.current.getBoundingClientRect();
    const left = end ? b.right - t.width : b.left - EDGE;
    const above = b.top - t.height - EDGE;
    setPos({
      left: Math.min(Math.max(left, EDGE), window.innerWidth - t.width - EDGE),
      top: above >= EDGE ? above : b.bottom + EDGE,
    });
    // The page moved under it: close rather than float away from its "?".
    const close = () => setOpen(false);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [open, end]);

  return (
    <span className="tip" onPointerEnter={() => setOpen(true)} onPointerLeave={() => setOpen(false)}>
      <button
        ref={button}
        type="button"
        className="tip-btn"
        aria-label="More info"
        aria-describedby={id}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
      >
        ?
      </button>
      {open &&
        createPortal(
          <span
            ref={tip}
            role="tooltip"
            id={id}
            className="tip-text"
            style={pos ? { left: pos.left, top: pos.top } : { left: 0, top: 0, visibility: "hidden" }}
          >
            {children}
          </span>,
          document.body,
        )}
    </span>
  );
}
