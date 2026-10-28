import { apiJson } from "./auth";
import type { Side } from "./theses";

// Mock for the public Build page, which renders on the server without the user's token.
/** Cash available to the signed-in account, in USD. */
export const ACCOUNT_BALANCE = 2500;

// Mirrors AccountOut, TradePreview and TradeResult in api/app/schemas.py (fields this UI uses).

export type AccountMode = "paper" | "live";

export interface Holding {
  market: { id: number };
  side: Side;
  contracts: number;
  cost: number;
  value: number;
}

export interface BasketHoldings {
  basket_id: number;
  value: number;
  cost: number;
  unrealized_pnl: number;
  holdings: Holding[];
}

export interface Account {
  mode: AccountMode;
  cash: number;
  equity: number;
  baskets: BasketHoldings[];
}

export interface PlannedOrder {
  market_id: number;
  side: Side;
  count: number;
  price: number;
  fee: number;
  // Negative for a buy.
  cash_delta: number;
}

export interface SkippedLeg {
  market_id: number;
  side: Side;
  reason: string;
}

export interface TradePreview {
  orders: PlannedOrder[];
  skipped: SkippedLeg[];
  cash_delta: number;
  cash_after: number;
}

export interface TradeResult {
  skipped: SkippedLeg[];
  account: Account;
}

export const getAccount = (mode: AccountMode = "paper") => apiJson<Account>(`/accounts/${mode}`);

const trade = <T>(mode: AccountMode, basketId: number, action: "buy" | "sell", body: object) =>
  apiJson<T>(`/accounts/${mode}/baskets/${basketId}/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

/** Price the orders `amount` dollars would place, without placing them. */
export const previewBuy = (basketId: number, amount: number, mode: AccountMode = "paper") =>
  trade<TradePreview>(mode, basketId, "buy", { amount, dry_run: true });

export const buyBasket = (basketId: number, amount: number, mode: AccountMode = "paper") =>
  trade<TradeResult>(mode, basketId, "buy", { amount });

export const sellBasket = (basketId: number, fraction = 1, mode: AccountMode = "paper") =>
  trade<TradeResult>(mode, basketId, "sell", { fraction });
