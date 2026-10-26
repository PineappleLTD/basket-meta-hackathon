export const pct = (x: number, digits = 0) => `${(x * 100).toFixed(digits)}%`;

export const signedPct = (x: number, digits = 1) => `${x >= 0 ? "+" : ""}${pct(x, digits)}`;

/** A 0..1 price as cents, e.g. 0.58 -> "58¢". */
export const cents = (price: number) => `${Math.round(price * 100)}¢`;

export const date = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });

/** Message to show for a failed API call. */
export const errorText = (err: unknown) =>
  err instanceof Error && err.message !== "Failed to fetch" ? err.message : "Couldn't reach the server";
