import type { Metadata } from "next";
import Link from "next/link";
import { AuthNav } from "@/components/AuthNav";
import { BacktestChart } from "@/components/BacktestChart";
import { DbStatus } from "@/components/DbStatus";
import { Logo } from "@/components/Logo";
import { BACKTEST, BACKTEST_WINDOW, median, signedPct, stdev } from "@/lib/backtest";

export const metadata: Metadata = { title: "Benchmarks · basket" };

const N = BACKTEST.length;
const baskets = BACKTEST.map((t) => t.basket);
const singles = BACKTEST.map((t) => t.single);
const pct = (v: number) => `${Math.round(v * 100)}%`;
const day = (iso: string) =>
  new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });

const STATS: [value: string, compare: string, label: string][] = [
  [`${BACKTEST.filter((t) => t.basket > t.single).length} of ${N}`, "", "ideas where the basket did better"],
  [pct(stdev(baskets)), `vs ${pct(stdev(singles))}`, "how much results varied from idea to idea"],
  [
    String(baskets.filter((v) => v < -0.2).length),
    `vs ${singles.filter((v) => v < -0.2).length}`,
    "ideas that lost more than 20%",
  ],
  [
    pct(median(BACKTEST.map((t) => t.basketDrawdown))),
    `vs ${pct(median(BACKTEST.map((t) => t.singleDrawdown)))}`,
    "typical biggest dip along the way",
  ],
];

// Mini range for each row of the table: the same scale as the big chart.
const MIN = -0.8;
const MAX = 1.0;
const at = (v: number) => `${((Math.min(Math.max(v, MIN), MAX) - MIN) / (MAX - MIN)) * 100}%`;

const ROWS = [...BACKTEST].sort((a, b) => b.basket - b.single - (a.basket - a.single));

export default function Page() {
  return (
    <main className="bench-page">
      <nav className="topbar">
        <div className="brand">
          <Link href="/" className="mark">
            <Logo />
            basket
          </Link>
          <DbStatus />
        </div>
        <AuthNav />
      </nav>

      <header className="bench-intro">
        <div className="eyebrow">Benchmarks</div>
        <h1>how baskets did in our backtest.</h1>
        <p>
          We picked {N} ideas, like Fed rate cuts and the midterms, and bet on each one in two ways. One was a basket
          of related Kalshi markets. The other was a single bet on the most popular market for that idea. We bought
          both on {day(BACKTEST_WINDOW.from)} and held them for {BACKTEST_WINDOW.days} days.
        </p>
      </header>

      <dl className="bench-stats">
        {STATS.map(([value, compare, label]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>
              {value} {compare && <span className="bench-vs">{compare}</span>}
            </dd>
          </div>
        ))}
      </dl>
      <p className="bench-key">
        <i className="bench-swatch basket" /> Basket <i className="bench-swatch single" /> Single bet
      </p>

      <section className="bench-section">
        <div className="bench-section-head">
          <h2>all {N} ideas, side by side.</h2>
          <p>
            Each dot is one idea&rsquo;s return after fees. The basket results sit fairly close together. The single
            bets are more spread out, from {signedPct(Math.min(...singles), 0)} to{" "}
            {signedPct(Math.max(...singles), 0)}, so how you did depended a lot on which market you picked. Hover over
            a dot to see both results for that idea.
          </p>
        </div>
        <div className="bench-card">
          <BacktestChart />
        </div>
      </section>

      <section className="bench-section">
        <div className="bench-section-head">
          <h2>results for each idea.</h2>
          <p>Sorted by how much better the basket did.</p>
        </div>
        <div className="bench-table-wrap">
          <table className="pt bench-table">
            <thead>
              <tr>
                <th scope="col">Idea</th>
                <th scope="col" className="num">
                  Markets
                </th>
                <th scope="col" className="num">
                  Basket
                </th>
                <th scope="col" className="num">
                  Single bet
                </th>
                <th scope="col" className="bench-range-col">
                  <span className="sr-only">Range</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {ROWS.map((t) => (
                <tr key={t.thesis}>
                  <td className="bench-view">{t.thesis}</td>
                  <td className="num">{t.markets}</td>
                  <td className="num mono">{signedPct(t.basket)}</td>
                  <td className="num mono bench-muted">{signedPct(t.single)}</td>
                  <td className="bench-range-col" aria-hidden="true">
                    <div className="bench-range">
                      <i className="bench-range-zero" style={{ left: at(0) }} />
                      <i
                        className="bench-range-bar"
                        style={{ left: at(Math.min(t.basket, t.single)), right: `calc(100% - ${at(Math.max(t.basket, t.single))})` }}
                      />
                      <i className="bench-range-dot single" style={{ left: at(t.single) }} />
                      <i className="bench-range-dot basket" style={{ left: at(t.basket) }} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="bench-section bench-notes">
        <div>
          <h2>how we tested.</h2>
          <ul>
            <li>
              {N} ideas in total. 6 are baskets people saved in the app, and 23 were built by our model from a
              one-line description.
            </li>
            <li>
              We bought both versions on {day(BACKTEST_WINDOW.from)} at market prices, including Kalshi&rsquo;s fee
              and the bid&ndash;ask spread.
            </li>
            <li>We held them until {day(BACKTEST_WINDOW.to)} without rebalancing.</li>
            <li>Basket weights only used prices from before the buy date.</li>
            <li>The single bet put everything into the basket&rsquo;s most traded market.</li>
          </ul>
        </div>
        <div>
          <h2>things to keep in mind.</h2>
          <ul>
            <li>
              Both lost a little money over this period. The median basket was {signedPct(median(baskets))} and the
              median single bet was {signedPct(median(singles))}. A basket spreads out your risk, but it won&rsquo;t
              make a wrong idea pay off.
            </li>
            <li>
              We picked markets from the ones open today, so we knew a bit more than someone buying in September
              would have.
            </li>
            <li>
              {N} ideas over {BACKTEST_WINDOW.days} days is a small test. Markets that closed keep their last price
              until they settle.
            </li>
          </ul>
        </div>
      </section>

      <section className="bench-cta">
        <h2>want to try one?</h2>
        <Link href="/basket" className="cta">
          build a basket <span aria-hidden="true">→</span>
        </Link>
      </section>
    </main>
  );
}
