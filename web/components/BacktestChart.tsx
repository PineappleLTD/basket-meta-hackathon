"use client";

import { useState } from "react";
import { BACKTEST, median, signedPct } from "@/lib/backtest";

// Every thesis's 45-day net return, one dot each: baskets on top, single bets below.
// Hover a dot to see that thesis in both rows.

const W = 720;
const H = 196;
const PAD = { left: 12, right: 12 };
const ROWS = [
  { key: "basket", label: "Basket", y: 52 },
  { key: "single", label: "Single bet", y: 128 },
] as const;
const AXIS_Y = 172;
const MIN = -0.8;
const MAX = 1.0;
const TICKS = [-0.75, -0.5, -0.25, 0, 0.25, 0.5, 0.75, 1];
const R = 5;
const GAP = R * 2 + 1;

const x = (v: number) => PAD.left + ((v - MIN) / (MAX - MIN)) * (W - PAD.left - PAD.right);

/** Vertical offsets so dots that would overlap stack above and below the row instead. */
function swarm(values: number[]): number[] {
  const order = values.map((_, i) => i).sort((a, b) => values[a] - values[b]);
  const placed: { cx: number; dy: number }[] = [];
  const dy = new Array<number>(values.length).fill(0);
  for (const i of order) {
    const cx = x(values[i]);
    const near = placed.filter((p) => Math.abs(p.cx - cx) < GAP);
    for (const step of [0, -1, 1, -2, 2, -3, 3]) {
      if (near.every((p) => Math.hypot(p.cx - cx, p.dy - step * GAP) >= GAP)) {
        dy[i] = step * GAP;
        break;
      }
    }
    placed.push({ cx, dy: dy[i] });
  }
  return dy;
}

const series = ROWS.map((row) => {
  const values = BACKTEST.map((t) => t[row.key]);
  return { ...row, values, offsets: swarm(values), median: median(values) };
});
const [basket, single] = series;

export function BacktestChart() {
  const [hover, setHover] = useState<number | null>(null);
  const t = hover == null ? null : BACKTEST[hover];

  return (
    <div className="bench-plot" onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Return after fees for each thesis, basket versus single bet">
        {TICKS.map((v) => (
          <g key={v}>
            <line className={v === 0 ? "bench-zero" : "bench-grid"} x1={x(v)} x2={x(v)} y1={16} y2={AXIS_Y - 8} />
            <text className="bench-tick" x={x(v)} y={AXIS_Y + 12} textAnchor="middle">
              {v === 0 ? "0%" : signedPct(v, 0)}
            </text>
          </g>
        ))}

        {series.map((s) => (
          <g key={s.key} className={`bench-row bench-${s.key}`}>
            <text className="bench-label" x={PAD.left} y={s.y - 30}>
              {s.label}
              <tspan className="bench-label-sub"> · median {signedPct(s.median)}</tspan>
            </text>
            <line className="bench-median" x1={x(s.median)} x2={x(s.median)} y1={s.y - 20} y2={s.y + 20} />
            {s.values.map((v, i) => (
              <circle
                key={i}
                className={`bench-dot${hover != null && hover !== i ? " dim" : ""}`}
                cx={x(v)}
                cy={s.y + s.offsets[i]}
                r={R}
              />
            ))}
            {hover != null && (
              <circle className="bench-ring" cx={x(s.values[hover])} cy={s.y + s.offsets[hover]} r={R + 3} />
            )}
          </g>
        ))}

        {hover != null && (
          <line
            className="bench-link"
            x1={x(basket.values[hover])}
            y1={basket.y + basket.offsets[hover] + R + 4}
            x2={x(single.values[hover])}
            y2={single.y + single.offsets[hover] - R - 4}
          />
        )}

        {/* Hit targets bigger than the dots, drawn last so they sit on top. */}
        {series.map((s) =>
          s.values.map((v, i) => (
            <circle
              key={`${s.key}-${i}`}
              className="bench-hit"
              cx={x(v)}
              cy={s.y + s.offsets[i]}
              r={R * 2}
              onMouseEnter={() => setHover(i)}
            />
          )),
        )}
      </svg>

      {t && hover != null && (
        <div className="bench-tip" style={{ left: `${Math.min((Math.min(x(t.basket), x(t.single)) / W) * 100, 62)}%` }}>
          <b>{t.thesis}</b>
          <span>
            Basket of {t.markets} <span className="mono">{signedPct(t.basket)}</span>
          </span>
          <span>
            Single bet <span className="mono">{signedPct(t.single)}</span>
          </span>
        </div>
      )}
    </div>
  );
}
