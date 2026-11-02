"use client";

import { useState } from "react";
import { BASE_NAV, type NavPoint } from "@/lib/baskets";
import { date } from "@/lib/format";
import { InfoTip } from "./InfoTip";

const W = 600;
const H = 160;

const time = (p: NavPoint) => new Date(p.ts).getTime();

// The basket's index value over time, with a dashed line at the starting 100. `mine` is the
// user's holding per share on the same scale, drawn over it. Hovering reports the nearest
// index point so the page header can show it.
export function NavChart({
  points,
  mine = [],
  onHover,
}: {
  points: NavPoint[];
  mine?: NavPoint[];
  onHover: (p: NavPoint | null) => void;
}) {
  const [active, setActive] = useState<number | null>(null);

  const ts = points.map(time);
  const t0 = ts[0];
  const span = ts[ts.length - 1] - t0;
  if (points.length < 2 || span <= 0) {
    return <p className="nav-empty">The chart fills in as prices update. Check back after the next sync.</p>;
  }

  const navs = points.map((p) => p.nav);
  const own = mine.filter((p) => time(p) >= t0);
  const lo = Math.min(BASE_NAV, ...navs, ...own.map((p) => p.nav));
  const hi = Math.max(BASE_NAV, ...navs, ...own.map((p) => p.nav));
  const pad = Math.max((hi - lo) * 0.15, 0.5);
  const x = (t: number) => ((Math.min(t, t0 + span) - t0) / span) * W;
  const y = (v: number) => H - ((v - (lo - pad)) / (hi - lo + 2 * pad)) * H;
  const path = (series: NavPoint[]) =>
    series.map((p, i) => `${i ? "L" : "M"}${x(time(p)).toFixed(1)},${y(p.nav).toFixed(1)}`).join("");
  const line = path(points);

  function track(e: React.PointerEvent<HTMLDivElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    const t = t0 + ((e.clientX - box.left) / box.width) * span;
    let best = 0;
    for (let i = 1; i < ts.length; i++) if (Math.abs(ts[i] - t) < Math.abs(ts[best] - t)) best = i;
    setActive(best);
    onHover(points[best]);
  }

  function leave() {
    setActive(null);
    onHover(null);
  }

  return (
    <figure className="nav-chart">
      <div className="nav-plot" onPointerMove={track} onPointerLeave={leave}>
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
          <line className="nav-base" x1={0} x2={W} y1={y(BASE_NAV)} y2={y(BASE_NAV)} />
          <path className="nav-line" d={line} />
          {own.length > 1 && <path className="nav-line nav-mine" d={path(own)} />}
          {active !== null && <line className="nav-cursor" x1={x(ts[active])} x2={x(ts[active])} y1={0} y2={H} />}
        </svg>
        {active !== null && (
          <i
            className="nav-dot"
            style={{ left: `${(x(ts[active]) / W) * 100}%`, top: `${(y(navs[active]) / H) * 100}%` }}
          />
        )}
        <span className="nav-base-label" style={{ top: `${(y(BASE_NAV) / H) * 100}%` }}>
          {BASE_NAV}
        </span>
      </div>
      <figcaption className="nav-axis">
        <span>{date(points[0].ts)}</span>
        {own.length > 1 && (
          <span className="nav-legend">
            <i className="nav-key" /> Index
            <i className="nav-key nav-key-mine" /> Your position
            <InfoTip>
              Index is the basket&rsquo;s NAV at mid prices, rebased to 100 at inception. Your position is your
              holding&rsquo;s value per share on the same scale. The gap between them is your execution cost:
              spreads, fees and whole-contract rounding.
            </InfoTip>
          </span>
        )}
        <span>{date(points[points.length - 1].ts)}</span>
      </figcaption>
    </figure>
  );
}
