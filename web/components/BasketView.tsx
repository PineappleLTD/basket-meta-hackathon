import { liquidityScore, pct, signedPct } from "@/lib/format";
import type { Contract } from "@/lib/types";
import { SectionHeader } from "./SectionHeader";

function ContractTable({ basket }: { basket: Contract[] }) {
  return (
    <div className="tbl-wrap">
      <table className="tbl">
        <thead>
          <tr>
            <th>Market</th>
            <th className="d-only">Venue</th>
            <th>Side</th>
            <th className="r">Price</th>
            <th className="r">Weight</th>
          </tr>
        </thead>
        <tbody>
          {basket.map((c) => (
            <tr key={c.ticker}>
              <td>
                <div className="q">{c.question}</div>
                <div className="tk">
                  {c.ticker}
                  <span className="m-only"> · {c.venue}</span>
                </div>
              </td>
              <td className="venue d-only">{c.venue}</td>
              <td>
                <span className={c.side === "No" ? "side no" : "side"}>{c.side}</span>
              </td>
              <td className="r num">{Math.round(c.price * 100)}¢</td>
              <td className="r num">{pct(c.weight)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function WeightBars({ basket }: { basket: Contract[] }) {
  const top = Math.max(...basket.map((c) => c.weight));
  return (
    <div className="bars">
      <div className="eyebrow">Allocation</div>
      {basket.map((c) => (
        <div key={c.ticker} className={c.weight === top ? "bar top" : "bar"}>
          <span className="lbl">{c.ticker}</span>
          <div className="trk">
            <div className="fill" style={{ width: pct(c.weight / top) }} />
          </div>
          <span className="val">{pct(c.weight)}</span>
        </div>
      ))}
    </div>
  );
}

function Stats({ basket }: { basket: Contract[] }) {
  const sum = (f: (c: Contract) => number) => basket.reduce((s, c) => s + c.weight * f(c), 0);
  const edge = sum((c) => (c.fair - c.price) / c.price);
  const liquidity = sum((c) => liquidityScore(c.liquidity));
  const venues = [...new Set(basket.map((c) => c.venue))].sort().join(" + ");
  return (
    <div className="stats">
      <div className="stat">
        <div className="eyebrow">Contracts</div>
        <div className="stat-v">{basket.length}</div>
        <div className="stat-n">{venues}</div>
      </div>
      <div className="stat">
        <div className="eyebrow">Expected value</div>
        <div className="stat-v accent">{signedPct(edge)}</div>
        <div className="stat-n">Model fair value vs. price</div>
      </div>
      <div className="stat">
        <div className="eyebrow">Liquidity score</div>
        <div className="stat-v">
          {Math.round(liquidity)}
          <small>/100</small>
        </div>
        <div className="stat-n">Volume-weighted, log scale</div>
      </div>
    </div>
  );
}

export function BasketView({ theme, basket }: { theme: string; basket: Contract[] }) {
  return (
    <section>
      <SectionHeader
        num="02"
        label={`Basket · ${theme}`}
        title="Your thesis, as a portfolio"
        lede="Contracts across venues, weighted for relevance, liquidity and correlation."
      />
      <ContractTable basket={basket} />
      <WeightBars basket={basket} />
      <Stats basket={basket} />
    </section>
  );
}
