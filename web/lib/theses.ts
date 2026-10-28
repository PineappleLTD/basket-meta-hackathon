import { apiJson } from "./auth";

// Mirrors ThesisSelection in api/app/schemas.py (fields this UI uses).

export type Side = "yes" | "no";

export interface Market {
  id: number;
  external_id: string;
  question: string;
  category: string | null;
  url: string | null;
  yes_price: number;
  volume: number;
  closes_at: string | null;
}

export interface PositionIn {
  market_id: number;
  side: Side;
  weight: number;
}

export interface Pick {
  market: Market;
  side: Side;
  weight: number;
  relevance: number;
  rationale: string;
  liquidity: number;
}

export interface Selection {
  thesis: string;
  summary: string;
  sub_themes: string[];
  candidates_considered: number;
  picks: Pick[];
  // Daily sd of the basket's returns.
  volatility: number;
  diversification_ratio: number;
  // Ready to send as `positions` to POST /baskets.
  positions: PositionIn[];
}

// Slow (up to a minute): the API makes two model calls.
export const selectPicks = (thesis: string) =>
  apiJson<Selection>("/theses/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ thesis }),
  });

/** Starter theses on the Build page. */
export const SUGGESTED_THESES = [
  "AI regulation",
  "US recession 2027",
  "Fed cuts by March",
  "Oil above $100",
  "Bitcoin above $150k",
  "Atlantic hurricane season",
  "SpaceX IPO in 2027",
  "Housing slowdown",
];

export const DEFAULT_THEME = SUGGESTED_THESES[0];

/** Typical time to match and weight a basket. */
export const BUILD_ETA_SECONDS = 8;
