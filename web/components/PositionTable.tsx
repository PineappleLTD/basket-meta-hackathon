import type { ReactNode } from "react";
import { cents, date, pct } from "@/lib/format";
import type { Market, Side } from "@/lib/theses";

export interface PositionRow {
  market: Market;
  side: Side;
  // Price of the side held, 0..1.
  price: number;
  // Share of the basket, 0..1.
  weight: number;
  rationale?: string;
  // Extra line under the market, e.g. what a buy would place.
  note?: ReactNode;
}

const sourceName = (url: string) => (url.includes("polymarket") ? "Polymarket" : "Kalshi");

// One row per market: what it asks, which side the basket takes, its price, and how much of the money goes there.
export function PositionTable({ rows }: { rows: PositionRow[] }) {
  return (
    <table className="pt">
      <thead>
        <tr>
          <th scope="col">Market</th>
          <th scope="col">Side</th>
          <th scope="col" className="num">Price</th>
          <th scope="col" className="num">Weight</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={`${r.market.id}-${r.side}`}>
            <td className="pt-market">
              <p className="pt-q">{r.market.question}</p>
              <p className="pt-sub">
                {[r.market.category, r.market.closes_at && `Closes ${date(r.market.closes_at)}`]
                  .filter(Boolean)
                  .join(" · ")}
                {r.market.url && (
                  <a href={r.market.url} target="_blank" rel="noreferrer">
                    {sourceName(r.market.url)} ↗
                  </a>
                )}
              </p>
              {r.rationale && <p className="pt-why">{r.rationale}</p>}
              {r.note && <p className="pt-note">{r.note}</p>}
            </td>
            <td>
              <span className={`side ${r.side}`}>{r.side === "yes" ? "Yes" : "No"}</span>
            </td>
            <td className="num">{cents(r.price)}</td>
            <td className="num">{pct(r.weight)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** How to read the table, shown above it. */
export function PositionLegend() {
  return (
    <p className="pt-legend">
      Each row is a bet on one market. <b>Yes</b> pays $1 a contract if it happens and <b>No</b> pays $1 if it
      doesn&rsquo;t. The price is what a contract costs now. <b>Weight</b> is the share of your money that goes into
      that bet; the weights add up to 100%.
    </p>
  );
}
