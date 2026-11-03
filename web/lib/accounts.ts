import { apiJson } from "./auth";
import type { Market, Side } from "./theses";

// Mirrors AccountOut, TradePreview, TradeResult and friends in api/app/schemas.py (fields this UI uses).

export type AccountMode = "paper" | "live";
export type Action = "buy" | "sell";

export interface Holding {
  market: Market;
  side: Side;
  contracts: number;
  cost: number;
  // At the mark (mid), and sold now at the best bid after fees.
  value: number;
  liquidation_value: number;
  weight: number;
  // The weight a rebalance would bring it to.
  target_weight: number;
}

export interface BasketHoldings {
  basket_id: number;
  nav: number;
  value: number;
  liquidation_value: number;
  cost: number;
  unrealized_pnl: number;
  realized_pnl: number;
  max_drift: number;
  // Value per share, comparable to the basket's NAV. Null while nothing is held.
  share_value: number | null;
  opened_at: string | null;
  // Since opened_at: the holding's return and the basket index's.
  holding_return: number | null;
  index_return: number | null;
  holdings: Holding[];
  cash: number;
}

export interface Account {
  mode: AccountMode;
  cash: number;
  available_cash: number;
  starting_cash: number;
  equity: number;
  liquidation_equity: number;
  pnl: number;
  realized_pnl: number;
  baskets: BasketHoldings[];
}

export interface PlannedOrder {
  market_id: number;
  side: Side;
  action: Action;
  count: number;
  // Average price per contract; best_price is the first contract's.
  price: number;
  best_price: number;
  price_impact: number;
  fee: number;
  // Negative for a buy.
  cash_delta: number;
}

export interface SkippedLeg {
  market_id: number;
  side: Side;
  reason: string;
}

export type TradeKind = "buy" | "sell" | "rebalance" | "settle";

export interface TradePreview {
  kind: TradeKind;
  orders: PlannedOrder[];
  skipped: SkippedLeg[];
  fees: number;
  price_impact: number;
  cash_delta: number;
  cash_after: number;
  basket_cash_delta: number;
}

export interface TradeResult {
  skipped: SkippedLeg[];
  account: Account;
}

export type OrderOrigin = "manual" | "recurring" | "auto_rebalance" | "conditional" | "settlement" | "news" | "arbitrage";

export interface Order {
  id: number;
  market_id: number;
  side: Side;
  action: Action;
  count: number;
  filled_count: number;
  fill_price: number | null;
  gross: number;
  fee: number;
  realized_pnl: number | null;
}

export interface BasketOrder {
  id: number;
  kind: TradeKind;
  origin: OrderOrigin;
  basket_cash_delta: number;
  // Dollars for a buy, share of holdings for a sell.
  amount: number | null;
  created_at: string;
  orders: Order[];
}

export interface StakePoint {
  ts: string;
  value: number;
  share_value: number | null;
  nav: number;
}

export interface Automation {
  // Rebalance once any holding drifts this far from target; null is off.
  auto_rebalance_drift: number | null;
  recurring_amount: number | null;
  recurring_days: number | null;
  next_recurring_at: string | null;
  note: string | null;
}

export type TriggerCondition = "at_or_below" | "at_or_above";

export interface ConditionalOrder {
  id: number;
  basket_id: number;
  action: Action;
  // Dollars for a buy, share of holdings for a sell.
  amount: number;
  condition: TriggerCondition;
  nav: number;
  status: "open" | "filled" | "cancelled" | "failed";
  created_at: string;
  triggered_at: string | null;
  note: string | null;
}

const json = (method: string, body: object): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

const base = (mode: AccountMode) => `/accounts/${mode}`;

export const getAccount = (mode: AccountMode = "paper") => apiJson<Account>(base(mode));

export const resetAccount = (mode: AccountMode = "paper") =>
  apiJson<Account>(`${base(mode)}/reset`, { method: "POST" });

function trade<T>(basketId: number, action: "buy" | "sell" | "rebalance", body: object, mode: AccountMode) {
  return apiJson<T>(`${base(mode)}/baskets/${basketId}/${action}`, json("POST", body));
}

/** Price the orders `amount` dollars would place, without placing them. */
export const previewBuy = (basketId: number, amount: number, mode: AccountMode = "paper") =>
  trade<TradePreview>(basketId, "buy", { amount, dry_run: true }, mode);

export const buyBasket = (basketId: number, amount: number, mode: AccountMode = "paper") =>
  trade<TradeResult>(basketId, "buy", { amount }, mode);

export const previewSell = (basketId: number, fraction: number, mode: AccountMode = "paper") =>
  trade<TradePreview>(basketId, "sell", { fraction, dry_run: true }, mode);

export const sellBasket = (basketId: number, fraction = 1, mode: AccountMode = "paper") =>
  trade<TradeResult>(basketId, "sell", { fraction }, mode);

export const previewRebalance = (basketId: number, mode: AccountMode = "paper") =>
  trade<TradePreview>(basketId, "rebalance", { dry_run: true }, mode);

export const rebalanceHoldings = (basketId: number, mode: AccountMode = "paper") =>
  trade<TradeResult>(basketId, "rebalance", {}, mode);

// Newest first.
export const listOrders = (basketId: number, mode: AccountMode = "paper") =>
  apiJson<BasketOrder[]>(`${base(mode)}/orders?basket_id=${basketId}`);

// Oldest first: after each trade and every sync.
export const getStakeHistory = (basketId: number, mode: AccountMode = "paper") =>
  apiJson<StakePoint[]>(`${base(mode)}/baskets/${basketId}/history`);

export const getAutomation = (basketId: number, mode: AccountMode = "paper") =>
  apiJson<Automation>(`${base(mode)}/baskets/${basketId}/automation`);

export interface AutomationIn {
  auto_rebalance_drift: number | null;
  recurring_amount: number | null;
  recurring_days: number | null;
}

export const setAutomation = (basketId: number, body: AutomationIn, mode: AccountMode = "paper") =>
  apiJson<Automation>(`${base(mode)}/baskets/${basketId}/automation`, json("PUT", body));

export const listConditionalOrders = (basketId: number, mode: AccountMode = "paper") =>
  apiJson<ConditionalOrder[]>(`${base(mode)}/conditional-orders?basket_id=${basketId}`);

export interface ConditionalOrderIn {
  action: Action;
  amount: number;
  condition: TriggerCondition;
  nav: number;
}

export const createConditionalOrder = (basketId: number, body: ConditionalOrderIn, mode: AccountMode = "paper") =>
  apiJson<ConditionalOrder>(`${base(mode)}/baskets/${basketId}/conditional-orders`, json("POST", body));

export const cancelConditionalOrder = (id: number, mode: AccountMode = "paper") =>
  apiJson<void>(`${base(mode)}/conditional-orders/${id}`, { method: "DELETE" });
