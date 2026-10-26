export const pct = (x: number, digits = 1) => `${(x * 100).toFixed(digits)}%`;

export const usd = (x: number) => x.toLocaleString("en-US", { style: "currency", currency: "USD" });

export const signedPct = (x: number) => `${x >= 0 ? "+" : ""}${pct(x)}`;

/** Probability-point move as cents, e.g. 0.035 -> "+3.5¢". */
export const cents = (move: number) => `${move >= 0 ? "+" : ""}${(move * 100).toFixed(1)}¢`;

export const ago = (minutes: number) => (minutes < 60 ? `${minutes}m` : `${Math.floor(minutes / 60)}h`);

/** 0 at $10k traded, 100 at $100M, log-scaled. */
export const liquidityScore = (volume: number) => Math.min(100, Math.max(0, 25 * Math.log10(volume / 10_000)));
