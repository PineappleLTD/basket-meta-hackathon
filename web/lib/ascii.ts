// Procedural ASCII texture: a noisy ridge, like a probability chart, drawn in · : - = + X.

/** A run of characters sharing one style: "" for the fill, "r" for the ridge, "o" for orange peaks. */
export type Run = [cls: "" | "r" | "o", text: string];

function hash(x: number, y: number, seed: number): number {
  let h = (Math.imul(x, 374761393) + Math.imul(y, 668265263) + Math.imul(seed, 982451653)) >>> 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177) >>> 0;
  return ((h ^ (h >>> 16)) >>> 0) / 0xffffffff;
}

/** Smoothed value noise in [0, 1]. */
function noise(x: number, y: number, seed: number): number {
  const ix = Math.floor(x), iy = Math.floor(y);
  const fx = x - ix, fy = y - iy;
  const sx = fx * fx * (3 - 2 * fx), sy = fy * fy * (3 - 2 * fy);
  const a = hash(ix, iy, seed), b = hash(ix + 1, iy, seed);
  const c = hash(ix, iy + 1, seed), d = hash(ix + 1, iy + 1, seed);
  const top = a + sx * (b - a), bottom = c + sx * (d - c);
  return top + sy * (bottom - top);
}

const OCTAVES: [scale: number, weight: number][] = [[26, 0.6], [9, 0.28], [3.5, 0.12]];

export function asciiTexture(cols = 150, rows = 12, seed = 4): Run[][] {
  const raw = Array.from({ length: cols }, (_, c) =>
    OCTAVES.reduce((sum, [s, w], i) => sum + noise(c / s, i * 3.1, seed) * w, 0),
  );
  const lo = Math.min(...raw), hi = Math.max(...raw);
  const ridge = raw.map((v) => Math.round((0.12 + (0.76 * (v - lo)) / (hi - lo)) * (rows - 1)));
  const isPeak = (c: number) =>
    c >= 3 && c < cols - 3 && ridge[c] > ridge[c - 1] && ridge[c] >= rows * 0.55 &&
    ridge[c] === Math.max(...ridge.slice(c - 3, c + 4));

  return Array.from({ length: rows }, (_, r) => {
    const y = rows - 1 - r;
    const runs: Run[] = [];
    for (let c = 0; c < cols; c++) {
      const h = ridge[c], prev = ridge[Math.max(c - 1, 0)];
      const n = noise(c / 3.5, r / 1.6, seed + 1);
      let cell: Run;
      if (y === h && isPeak(c)) cell = ["o", "X"];
      else if (y >= Math.min(h, prev) && y <= Math.max(h, prev)) cell = ["r", "+"];
      else if (y < h) cell = ["", " ·:-="[Math.min(4, Math.floor((0.5 * n + (0.5 * y) / h) * 5))]];
      else cell = ["", n > 0.85 ? "·" : " "];
      const last = runs[runs.length - 1];
      if (last && last[0] === cell[0]) last[1] += cell[1];
      else runs.push(cell);
    }
    return runs;
  });
}
