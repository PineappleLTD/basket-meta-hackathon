import { apiJson } from "./auth";
import type { Market, PositionIn, Side } from "./theses";

// Mirrors BasketOut in api/app/schemas.py (fields this UI uses).

export interface Position {
  market: Market;
  side: Side;
  target_weight: number;
  current_weight: number;
  // Price of the side held, 0..1.
  price: number;
}

export interface Basket {
  id: number;
  name: string;
  description: string | null;
  created_at: string;
  // Starts at 100 when the basket is created.
  nav: number;
  positions: Position[];
}

export const BASE_NAV = 100;

export const listBaskets = () => apiJson<Basket[]>("/baskets");

export const getBasket = (id: number | string) => apiJson<Basket>(`/baskets/${id}`);

// A null name saves as "New basket"; titleBasket then names it.
export const createBasket = (name: string | null, description: string, positions: PositionIn[]) =>
  apiJson<Basket>("/baskets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, description, positions }),
  });

// Renames the basket to a short model-written title. Takes about a second.
export const titleBasket = (id: number) => apiJson<Basket>(`/baskets/${id}/title`, { method: "POST" });

export const deleteBasket = (id: number) => apiJson<void>(`/baskets/${id}`, { method: "DELETE" });
