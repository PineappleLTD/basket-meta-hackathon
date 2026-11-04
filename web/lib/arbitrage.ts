import { apiJson } from "./auth";
import type { Account, AccountMode, BasketOrder, PlannedOrder } from "./accounts";
import type { Market, Side } from "./theses";

// Hides the arbitrage scanner from the UI (sidebar link, /arbitrage, "add sets" on lock baskets).
// The API and the code stay in place; set to true to bring it back.
export const ARBITRAGE_ENABLED = false;

// Mirrors OpportunityOut, ArbitrageOut, LockOut and friends in api/app/schemas.py (fields this UI uses).

export type ArbKind = "ladder" | "bracket_no" | "bracket_yes";

export const KIND_LABEL: Record<ArbKind, string> = {
  ladder: "Strike ladder",
  bracket_no: "Bracket · NO on each",
  bracket_yes: "Bracket · YES on each",
};

export interface ArbLeg {
  market_id: number;
  ticker: string;
  question: string;
  // The outcome the market names when its question is shared, e.g. "Above 3.75%".
  outcome: string | null;
  url: string | null;
  side: Side;
  best_price: number;
  avg_price: number | null;
  closes_at: string | null;
}

export interface Opportunity {
  id: string;
  kind: ArbKind;
  title: string;
  explanation: string;
  legs: ArbLeg[];
  // One set is one contract of every leg; it pays at least this whatever happens.
  payout_per_set: number;
  edge_per_set: number;
  // The most profitable size against the live order books. Null when depth is unknown.
  sets: number | null;
  cost: number | null;
  payout: number | null;
  profit: number | null;
  return_pct: number | null;
  // Simple (not compounded) yearly rate until the last leg closes.
  annualized: number | null;
  closes_at: string | null;
  confirmed: boolean;
  first_seen: string;
  last_seen: string;
  times_seen: number;
  best_profit: number;
  note: string | null;
}

export interface Scan {
  started_at: string;
  duration_ms: number;
  markets: number;
  groups: number;
  candidates: number;
  found: number;
  error: string | null;
}

export interface ArbSummary {
  scans: number;
  last_scan: Scan | null;
  opportunities_seen: number;
  active: number;
  profit_available: number;
  best_profit_seen: number;
  locks: number;
  locked_cost: number;
  locked_payout: number;
  locked_profit: number;
}

export interface Arbitrage {
  summary: ArbSummary;
  opportunities: Opportunity[];
}

export interface Lock {
  basket_id: number;
  opportunity_id: string;
  kind: ArbKind;
  title: string;
  explanation: string;
  payout_per_set: number;
  // Totals over every lock placed into the basket.
  sets: number;
  cost: number;
  payout: number;
  profit: number;
  closes_at: string | null;
  created_at: string;
  // "locked" while held, then "settled" (paid out) or "closed" (sold). Only from the locks list.
  status: "locked" | "settled" | "closed" | null;
  held_sets: number | null;
}

export interface LockPreview {
  sets: number;
  orders: PlannedOrder[];
  cost: number;
  payout: number;
  profit: number;
  fees: number;
  return_pct: number;
  annualized: number | null;
  cash_after: number;
}

export interface LockResult {
  lock: Lock;
  basket_order: BasketOrder;
  filled_sets: number;
  account: Account;
}

const post = (body: object): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const getArbitrage = () => apiJson<Arbitrage>("/arbitrage");

// `sync` pulls fresh markets from Kalshi first, which takes about half a minute.
export const rescan = (sync = false) => apiJson<Arbitrage>("/arbitrage/scan", post({ sync }));

export const listLocks = (mode: AccountMode = "paper") => apiJson<Lock[]>(`/accounts/${mode}/arbitrage/locks`);

// Without `sets`, the most profitable size the order books allow.
export const previewLock = (id: string, sets: number | null, mode: AccountMode = "paper") =>
  apiJson<LockPreview>(`/accounts/${mode}/arbitrage/${id}/lock`, post({ sets, dry_run: true }));

export const placeLock = (id: string, sets: number | null, mode: AccountMode = "paper") =>
  apiJson<LockResult>(`/accounts/${mode}/arbitrage/${id}/lock`, post({ sets }));

/** "YES on Above 3.75%": a leg as a person would say it. */
export const legLabel = (leg: ArbLeg) => `${leg.side === "yes" ? "YES" : "NO"} on ${leg.outcome ?? leg.question}`;
