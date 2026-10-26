import Link from "next/link";
import { AuthNav } from "@/components/AuthNav";
import { DbStatus } from "@/components/DbStatus";
import { MarketGraph } from "@/components/MarketGraph";
import { Logo } from "@/components/Logo";

const MARKETS: [
  exchange: string,
  title: string,
  price: number,
  keep: boolean,
][] = [
  ["Kalshi", "LeBron plays in the 2027–28 season", 58, true],
  ["Poly", "LeBron signs a new contract by July", 44, true],
  ["Kalshi", "LeBron announces retirement in 2027", 31, true],
  ["Poly", "Lakers win the 2027 title", 9, false],
  ["Poly", "Bronny averages 10+ points", 12, false],
];

const BASKET: [title: string, side: string, weight: number][] = [
  ["Plays in 2027–28", "yes", 40],
  ["New contract by July", "yes", 35],
  ["Retires in 2027", "no", 25],
];

function FlowArrow() {
  return (
    <svg className="fl-arrow" viewBox="0 0 40 12" aria-hidden="true">
      <path
        d="M0 6h36M31 1l5 5-5 5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.25"
      />
    </svg>
  );
}

export default function Home() {
  return (
    <main className="home">
      <nav className="topbar">
        <div className="brand">
          <span className="mark">
            <Logo />
            basket
          </span>
          <DbStatus />
        </div>
        <AuthNav />
      </nav>

      <section className="welcome">
        <div className="welcome-copy">
          <div className="eyebrow">Index funds for prediction markets</div>
          <h1>place bets on your ideas,</h1>
          <p className="sub">
            type a view like &ldquo;LeBron never retires.&rdquo; we find the
            related contracts on Polymarket and Kalshi, combine them into one
            basket you can buy, and adjust it based on the news for you
          </p>
          <Link href="/basket" className="cta">
            build a basket <span aria-hidden="true">→</span>
          </Link>
        </div>
        <MarketGraph />
      </section>

      <section className="explain">
        <div className="explain-head">
          <div className="eyebrow">How it works</div>
          <h2>one idea can span many markets.</h2>
          <p className="sub">
            a single prediction market is a bet on one question. and a basket
            spreads your view across many questions that depends on it, so one
            surprise doesn&rsquo;t wipe you out.
          </p>
        </div>

        <figure className="flow">
          <div className="fl-col">
            <div className="eyebrow">1 · your view</div>
            <div className="fl-card fl-view">
              &ldquo;LeBron never retires.&rdquo;
            </div>
          </div>

          <FlowArrow />

          <div className="fl-col">
            <div className="eyebrow">2 · markets we scan</div>
            <ul className="fl-card fl-markets">
              {MARKETS.map(([ex, title, price, keep]) => (
                <li key={title} className={keep ? "" : "skip"}>
                  <span className="fl-ex">{ex}</span>
                  <span className="fl-title">{title}</span>
                  <span className="mono">{keep ? `${price}¢` : "–"}</span>
                </li>
              ))}
            </ul>
          </div>

          <FlowArrow />

          <div className="fl-col">
            <div className="eyebrow">3 · your basket</div>
            <ul className="fl-card fl-basket">
              {BASKET.map(([title, side, weight]) => (
                <li key={title}>
                  <div className="fl-row">
                    <span>
                      {title} <span className="fl-side">{side}</span>
                    </span>
                    <span className="mono">{weight}%</span>
                  </div>
                  <div className="fl-track">
                    <i style={{ width: `${weight}%` }} />
                  </div>
                </li>
              ))}
            </ul>
          </div>

          <div className="fl-loop">
            <div className="eyebrow">then, every time news breaks</div>
            <ol>
              <li>
                <span className="mono">headline</span>
                &ldquo;LeBron: &lsquo;I still love the grind&rsquo;&rdquo;
              </li>
              <li>
                <span className="mono">our estimate</span>
                &ldquo;Plays in 2027&ndash;28&rdquo; should be ~6 pts higher
              </li>
              <li>
                <span className="mono">basket</span>
                weight moves 40% &rarr; 46%
              </li>
            </ol>
          </div>
        </figure>

        <div className="explain-cta">
          <h2>got a take?</h2>
          <Link href="/basket" className="cta">
            build a basket <span aria-hidden="true">→</span>
          </Link>
        </div>
      </section>
    </main>
  );
}
