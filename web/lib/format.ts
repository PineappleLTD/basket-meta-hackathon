export const pct = (x: number, digits = 0) => `${(x * 100).toFixed(digits)}%`;

export const usd = (x: number) => x.toLocaleString("en-US", { style: "currency", currency: "USD" });

export const signedPct = (x: number, digits = 1) => `${x >= 0 ? "+" : ""}${pct(x, digits)}`;

/** Dollars with a sign either way, e.g. "+$1.20" or "−$3.00". */
export const signedUsd = (x: number) => `${x >= 0 ? "+" : "−"}${usd(Math.abs(x))}`;

/** A 0..1 price as cents, e.g. 0.58 -> "58¢". */
export const cents = (price: number) => `${Math.round(price * 100)}¢`;

export const date = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });

/** Message to show for a failed API call. */
export const errorText = (err: unknown) =>
  err instanceof Error && err.message !== "Failed to fetch" ? err.message : "Couldn't reach the server";

export const dateTime = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
