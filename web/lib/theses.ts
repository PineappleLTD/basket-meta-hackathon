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
  // Sent with saved baskets and holdings; a resolved market has its YES payout.
  is_open?: boolean;
  settlement_price?: number | null;
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
