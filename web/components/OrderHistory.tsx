import type { BasketOrder, Order, OrderOrigin, TradeKind } from "@/lib/accounts";
import { cents, dateTime, signedUsd, usd } from "@/lib/format";

const KIND: Record<TradeKind, string> = {
  buy: "Bought",
  sell: "Sold",
  rebalance: "Rebalanced",
  settle: "Paid out",
};

const ORIGIN: Record<OrderOrigin, string | null> = {
  manual: null,
  recurring: "Recurring",
  auto_rebalance: "Automatic",
  conditional: "Price trigger",
  settlement: "Market resolved",
};

// Cash an order moved: negative for a buy.
const cash = (o: Order) => (o.action === "buy" ? -(o.gross + o.fee) : o.gross - o.fee);

// Every trade in this basket, newest first; open one to see its fills.
export function OrderHistory({ orders, question }: { orders: BasketOrder[]; question: (marketId: number) => string }) {
  if (orders.length === 0) return <p className="bd-foot">Trades you place in this basket show up here.</p>;
  return (
    <ul className="hist">
      {orders.map((bo) => {
        const net = bo.orders.reduce((s, o) => s + cash(o), 0);
        const realized = bo.orders.reduce((s, o) => s + (o.realized_pnl ?? 0), 0);
        const hasRealized = bo.orders.some((o) => o.realized_pnl !== null);
        const unfilled = bo.orders.filter((o) => o.filled_count < o.count).length;
        const origin = ORIGIN[bo.origin];
        return (
          <li key={bo.id}>
            <details>
              <summary className="hist-row">
                <span className="hist-what">
                  {KIND[bo.kind]}
                  {origin && <span className="tag">{origin}</span>}
                </span>
                <span className="hist-when">{dateTime(bo.created_at)}</span>
                <span className="hist-cash">{signedUsd(net)}</span>
                {hasRealized && (
                  <span className={`hist-pnl ${realized < 0 ? "down" : "up"}`}>{signedUsd(realized)} realized</span>
                )}
              </summary>
              <ul className="hist-legs">
                {bo.orders.map((o) => (
                  <li key={o.id}>
                    <span className="hist-q">{question(o.market_id)}</span>
                    <span>
                      {o.action === "buy" ? "Bought" : bo.kind === "settle" ? "Paid out" : "Sold"} {o.filled_count}
                      {o.filled_count < o.count && ` of ${o.count}`} {o.side === "yes" ? "Yes" : "No"}
                      {o.fill_price !== null && ` at ${cents(o.fill_price)}`}
                      {o.fee > 0 && ` · fee ${usd(o.fee)}`}
                    </span>
                  </li>
                ))}
              </ul>
              {unfilled > 0 && (
                <p className="bd-foot">
                  {unfilled} {unfilled === 1 ? "order" : "orders"} filled only partly: the book moved before it was placed.
                </p>
              )}
            </details>
          </li>
        );
      })}
    </ul>
  );
}
