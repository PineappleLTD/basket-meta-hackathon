"use client";

import { useEffect, useRef, useState } from "react";
import { setSidebarWidth, SIDEBAR_DEFAULT, SIDEBAR_MAX, SIDEBAR_MIN } from "@/lib/sidebar";

const KEY_STEP = 16;

// Draggable edge between the sidebar and the page. Double-click puts it back to the default width.
export function SidebarResizer() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(SIDEBAR_DEFAULT);

  // The head script may already have applied a saved width, so read what actually rendered.
  useEffect(() => {
    if (ref.current?.parentElement) setWidth(ref.current.parentElement.offsetWidth);
  }, []);

  const resize = (next: number) => setWidth(setSidebarWidth(next));

  return (
    <div
      ref={ref}
      className="sidebar-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize baskets sidebar"
      aria-valuenow={width}
      aria-valuemin={SIDEBAR_MIN}
      aria-valuemax={SIDEBAR_MAX}
      tabIndex={0}
      title="Drag to resize, double-click to reset"
      // Stops the drag from selecting text, and leaves focus in the composer.
      onMouseDown={(e) => e.preventDefault()}
      onPointerDown={(e) => {
        if (e.button === 0) e.currentTarget.setPointerCapture(e.pointerId);
      }}
      // The sidebar starts at the window's left edge, so the pointer's x is its new width.
      onPointerMove={(e) => {
        if (e.currentTarget.hasPointerCapture(e.pointerId)) resize(e.clientX);
      }}
      onDoubleClick={() => resize(SIDEBAR_DEFAULT)}
      onKeyDown={(e) => {
        if (e.key === "ArrowLeft") resize(width - KEY_STEP);
        else if (e.key === "ArrowRight") resize(width + KEY_STEP);
        else return;
        e.preventDefault();
      }}
    />
  );
}
