"use client";

import { useEffect, useRef } from "react";

// Decorative only: a basket of contract blocks. Stacks grow at random, rebalance to one equal height,
// glow once even, then sink and repeat. Colors are CSS variables on .bk, so themes need nothing from here.

const NS = "http://www.w3.org/2000/svg";
const LIGHT = ["var(--bk-a-top)", "var(--bk-a-left)", "var(--bk-a-right)"];
const DARK = ["var(--bk-b-top)", "var(--bk-b-left)", "var(--bk-b-right)"];
const FACES = ["0,16.7 29,33.4 29,61.4 0,44.7", "29,33.4 58,16.7 58,44.7 29,61.4", "29,0 58,16.7 29,33.4 0,16.7"];
const STILL_AT = 13; // seconds into the loop to show when motion is reduced

const SX = 34, SY = 19.6, W = 29, H = 16.7, D = 28, CX = 360, CY = 330, S = 100 / 640;
const STEP = 0.42, GROW = 0.6, REB = 10.8, CYC = 17.2;

const SLOTS: [number, number][] = [];
for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) SLOTS.push([i, j]);
// Back to front, so nearer stacks paint over farther ones.
const ORDER = SLOTS.map((_, k) => k).sort((a, b) => SLOTS[a][0] + SLOTS[a][1] - (SLOTS[b][0] + SLOTS[b][1]) || SLOTS[a][0] - SLOTS[b][0]);

const rand = (n: number) => {
  const x = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
};
const ease = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x));
const round = (v: number, places = 3) => Math.round(v * 10 ** places) / 10 ** places;

type Cube = { l: number; t: number; o: number; top: string; left: string; right: string };

function frame(t: number): Cube[] {
  const c = Math.floor(t / CYC);
  const u = t - c * CYC;
  const target = SLOTS.map((_, k) => 1 + Math.floor(rand(c * 31 + k) * 6));
  target[Math.floor(rand(c * 7 + 2) * 9)] = 6; // always one tall stack
  target[Math.floor(rand(c * 7 + 5) * 9)] = 1; // and one short one
  const eq = Math.round(target.reduce((x, y) => x + y, 0) / 9);
  const maxMove = Math.max(...target.map((tg) => Math.abs(eq - tg)));
  const balanced = REB + maxMove * STEP + 0.3; // moment the basket is even
  const reset = balanced + 2.6; // hold the balanced state, then sink

  const heights = target.map((tg, k) => {
    if (u < REB) {
      // Each stack starts at its own moment; every block then takes its own random time to land.
      let at = GROW + rand(c * 13 + k) * 2.4;
      let height = 1;
      for (let b = 1; b < tg; b++) {
        const dur = 0.6 + rand(c * 97 + k * 11 + b) * 0.95;
        if (u < at) break;
        if (u < at + dur) {
          height += (u - at) / dur;
          break;
        }
        height += 1;
        at += dur + rand(c * 53 + k * 7 + b) * 0.35;
      }
      return Math.min(tg, height);
    }
    if (u < reset) {
      const n = eq - tg;
      return tg + Math.sign(n) * Math.min(Math.abs(n), (u - REB) / STEP);
    }
    return Math.max(1, eq - (u - reset) / 0.3);
  });

  const cubes: Cube[] = [];
  ORDER.forEach((k, oi) => {
    const [i, j] = SLOTS[k];
    const x = CX + (i - 1 - (j - 1)) * SX;
    const y = CY + (i - 1 + (j - 1)) * SY;
    const [top, left, right] = (i + j) % 2 === 0 ? LIGHT : DARK;
    // "Balanced" wave: tops glow orange back to front once the basket is even.
    const wave = u - balanced - oi * 0.09;
    const glow = wave > 0 && wave < 0.9 ? Math.sin((wave / 0.9) * Math.PI) * 0.9 : 0;
    const glowing = glow > 0.01 ? `color-mix(in srgb, var(--accent) ${round(glow * 100, 1)}%, ${top})` : top;
    const full = Math.floor(heights[k] + 1e-6);
    const frac = heights[k] - full;
    for (let level = 0; level < full; level++) {
      cubes.push({ l: round((x - W) * S), t: round((y - H - level * D) * S), o: 1, top: glowing, left, right });
    }
    if (frac > 0.01) {
      const e = ease(frac);
      cubes.push({ l: round((x - W) * S), t: round((y - H - full * D - (1 - e) * 22) * S), o: round(e), top, left, right });
    }
  });
  return cubes;
}

// The tray's woven front: two rails along each face, crossed by evenly spaced ticks.
const FRONT_TICKS = Array.from({ length: 11 }, (_, n) => {
  const along = n + 1;
  const drop = (along / 12) * 68.6;
  return {
    left: { x: round(241 + (along * 119) / 12, 1), y: round(328 + drop, 1) },
    right: { x: round(360 + (along * 119) / 12, 1), y: round(396.6 - drop, 1) },
  };
});

export function BasketAnimation() {
  const stageRef = useRef<HTMLDivElement>(null);
  const layerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const stage = stageRef.current;
    const layer = layerRef.current;
    if (!stage || !layer) return;
    const pool: SVGSVGElement[] = [];

    const makeCube = () => {
      const svg = document.createElementNS(NS, "svg");
      svg.setAttribute("viewBox", "0 0 58 61.4");
      svg.setAttribute("class", "bk-cube");
      FACES.forEach((points) => {
        const face = document.createElementNS(NS, "polygon");
        face.setAttribute("points", points);
        svg.appendChild(face);
      });
      layer.appendChild(svg);
      return svg;
    };

    const draw = (t: number) => {
      const cubes = frame(t);
      while (pool.length < cubes.length) pool.push(makeCube());
      pool.forEach((el, i) => {
        const c = cubes[i];
        if (!c) {
          el.style.display = "none";
          return;
        }
        el.style.display = "";
        el.style.left = `${c.l}cqw`;
        el.style.top = `${c.t}cqw`;
        el.style.opacity = String(c.o);
        const [left, right, top] = Array.from(el.children) as SVGElement[];
        left.style.fill = c.left;
        right.style.fill = c.right;
        top.style.fill = c.top;
      });
    };

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      draw(STILL_AT);
      return () => layer.replaceChildren();
    }

    let raf = 0;
    const t0 = performance.now();
    const loop = (now: number) => {
      draw((now - t0) / 1000);
      raf = requestAnimationFrame(loop);
    };
    const io = new IntersectionObserver(([e]) => {
      cancelAnimationFrame(raf);
      raf = e.isIntersecting ? requestAnimationFrame(loop) : 0;
    });
    io.observe(stage);

    return () => {
      cancelAnimationFrame(raf);
      io.disconnect();
      layer.replaceChildren();
    };
  }, []);

  return (
    <div className="bk">
      <div
        ref={stageRef}
        className="bk-stage"
        role="img"
        aria-label="A basket of contract blocks: stacks grow at random, then rebalance to one equal height"
      >
        <svg viewBox="0 0 640 540" aria-hidden="true">
          <path className="bk-flow" d="M178 170 C 270 180, 320 180, 360 220" />
          <polygon className="bk-side bk-f2" points="76,150 140,187 140,197 76,160" />
          <polygon className="bk-side bk-f4" points="140,187 204,150 204,160 140,197" />
          <polygon className="bk-thesis-top" points="140,113 204,150 140,187 76,150" />
          <polygon className="bk-thesis-inset" points="140,129.7 175.2,150 140,170.3 104.8,150" />
          <rect className="bk-input" x="125" y="142" width="30" height="16" rx="3" />
          <line className="bk-input bk-caret" x1="134" y1="146" x2="134" y2="154" />

          <polygon className="bk-wall bk-f3" points="360,289.4 479,358 360,426.6 241,358" />
          <polygon className="bk-wall bk-f1" points="360,289.4 241,358 241,328 360,259.4" />
          <polygon className="bk-wall bk-f4" points="360,289.4 479,358 479,328 360,259.4" />
          <polyline className="bk-rim" points="241,328 360,259.4 479,328" />
        </svg>

        <div ref={layerRef} />

        <svg viewBox="0 0 640 540" aria-hidden="true">
          <polygon className="bk-side bk-f4 bk-front" points="241,358 360,426.6 360,396.6 241,328" />
          <line className="bk-rail" x1="241" y1="348.1" x2="360" y2="416.7" />
          <line className="bk-rail" x1="241" y1="338.2" x2="360" y2="406.8" />
          <polygon className="bk-side bk-f5 bk-front" points="360,426.6 479,358 479,328 360,396.6" />
          <line className="bk-rail" x1="360" y1="416.7" x2="479" y2="348.1" />
          <line className="bk-rail" x1="360" y1="406.8" x2="479" y2="338.2" />
          {FRONT_TICKS.map(({ left, right }) => (
            <g key={left.x} className="bk-tick">
              <line x1={left.x} y1={left.y + 4} x2={left.x} y2={left.y + 27} />
              <line x1={right.x} y1={right.y + 4} x2={right.x} y2={right.y + 27} />
            </g>
          ))}
          <polyline className="bk-rim" points="241,328 360,396.6 479,328" />

          <g className="bk-labels">
            <polyline points="120,201 94,230 80,230" />
            <text x="20" y="250" className="bk-label-head">One thesis</text>
            <text x="20" y="270">“AI regulation tightens</text>
            <text x="20" y="287">by 2027.”</text>

            <polyline points="420,190 446,150 470,150" />
            <text x="476" y="132" className="bk-label-head">Many contracts</text>
            <text x="476" y="152">Each block is one contract.</text>
            <rect className="bk-swatch-a" x="476" y="182" width="10" height="10" rx="2" />
            <text x="492" y="191" className="bk-label-mono">Polymarket</text>
            <rect className="bk-swatch-b" x="568" y="182" width="10" height="10" rx="2" />
            <text x="584" y="191" className="bk-label-mono">Kalshi</text>

            <polyline points="247,348 211,398 191,398" />
            <text x="20" y="416" className="bk-label-head">Weighted stacks</text>
            <text x="20" y="436">More conviction, taller stack.</text>
            <text x="20" y="453">No single block carries it.</text>

            <polyline points="473,352 509,408 529,408" />
            <text x="630" y="426" textAnchor="end" className="bk-label-head">Rebalanced on news</text>
            <text x="630" y="446" textAnchor="end">When a headline moves prices,</text>
            <text x="630" y="463" textAnchor="end">blocks move with it.</text>
          </g>
        </svg>
      </div>
    </div>
  );
}
