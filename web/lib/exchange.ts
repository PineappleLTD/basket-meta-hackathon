import { apiJson } from "./auth";
import type { NewsCycle, Shift } from "./climate";

export interface ExchangeStatus { implemented: boolean; enabled_for_user: boolean; environment: "demo" | "production" | null }
export interface ExchangeState {
  armed: boolean;
  cycle: NewsCycle | null;
  orders: { client_id: string; status: string; phase: string; requested: number; confirmed: number; note: string | null }[];
  basket_cash: number;
  account_cash: number;
  proposal: { id: string; shifts: Shift[]; reason: string | null };
  cost_basis: string;
}
const send = <T>(path: string, body?: unknown, method = "POST") => apiJson<T>(path, {
  method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body),
});
export const exchangeStatus = () => apiJson<ExchangeStatus>("/exchange/status");
export const exchangeState = (id: number) => apiJson<ExchangeState>(`/exchange/baskets/${id}`);
export const connectExchange = (id: number) => send("/exchange/connect", { basket_id: id });
export const startExchange = (id: number, expectedId: string) => send(`/exchange/baskets/${id}/apply`, { expected_id: expectedId });
export const armExchange = (id: number, enabled: boolean) => send(`/exchange/baskets/${id}/automation`, { enabled }, "PUT");
export const cancelExchange = (id: number) => send(`/exchange/baskets/${id}/cancel`);
export const reviewExchange = (id: number) => send(`/exchange/baskets/${id}/review`);
export const reconcileExchange = (id: number) => send(`/exchange/baskets/${id}/advance`);
