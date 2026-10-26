export type Venue = "Polymarket" | "Kalshi";
export type Side = "Yes" | "No";

/** One position in a basket. Prices and fair values are probabilities on the held side. */
export interface Contract {
  ticker: string;
  question: string;
  venue: Venue;
  side: Side;
  price: number;
  fair: number;
  /** Traded volume in USD. */
  liquidity: number;
  /** Share of the basket, 0–1. Weights sum to 1. */
  weight: number;
}

/** A headline scored against the basket contract it touches. Moves are in probability points. */
export interface NewsSignal {
  minutesAgo: number;
  source: string;
  headline: string;
  ticker: string;
  question: string;
  predicted: number;
  actual: number;
  gap: number;
  rebalance: boolean;
}
