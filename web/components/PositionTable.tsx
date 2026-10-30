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
  // Extra line under the bet, e.g. what a buy would place.
  note?: ReactNode;
}

const sourceName = (url: string) => (url.includes("polymarket") ? "Polymarket" : "Kalshi");

// One row per market: what it asks, which side the basket takes, and how much of the money goes there.
export function PositionTable({ rows }: { rows: PositionRow[] }) {
  return (
    <div className="pt" role="table" aria-label="Positions">
      <div className="pt-row pt-head mono" role="row">
        <span role="columnheader">Market</span>
        <span role="columnheader">Bet</span>
        <span role="columnheader">Weight</span>
      </div>
      {rows.map((r) => (
        <div className="pt-row" role="row" key={`${r.market.id}-${r.side}`}>
          <div className="pt-market" role="cell">
            <p className="pt-q" title={r.market.question}>
              {r.market.question}
            </p>
            <p className="pt-sub mono">
              {[r.market.category, r.market.closes_at && `closes ${date(r.market.closes_at)}`]
                .filter(Boolean)
                .join(" · ")}
              {r.market.url && (
                <a href={r.market.url} target="_blank" rel="noreferrer">
                  view on {sourceName(r.market.url)} ↗
                </a>
              )}
            </p>
            {r.rationale && <p className="pt-why">{r.rationale}</p>}
          </div>
          <div className="pt-bet" role="cell">
            <span className={`side ${r.side}`}>{r.side}</span>
            <span className="mono pt-price">at {cents(r.price)}</span>
            {r.note && <span className="pt-note">{r.note}</span>}
          </div>
          <div className="pt-weight" role="cell">
            <span className="mono">{pct(r.weight)}</span>
            <div className="fl-track">
              <i style={{ width: pct(r.weight) }} />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

/** How to read the table, shown above it. */
export function PositionLegend() {
  return (
    <p className="pt-legend">
      Each row is a bet on one market. <b>YES</b> pays $1 a contract if it happens and <b>NO</b> pays $1 if it
      doesn&rsquo;t. The price is what a contract costs now. <b>Weight</b> is the share of your money that goes into
      that bet; the weights add up to 100%.
    </p>
  );
}
