import { asciiTexture } from "@/lib/ascii";

const TEXTURE = asciiTexture(150, 12, 12);

export function Hero() {
  return (
    <>
      <nav className="topbar">
        <span className="mark">
          <i />
          Basket
        </span>
        <span className="eyebrow">Polymarket · Kalshi</span>
      </nav>
      <section className="hero">
        <div className="eyebrow">Thematic prediction-market baskets</div>
        <h1>
          Bet on ideas,
          <br />
          not events<span className="dot">.</span>
        </h1>
        <p className="sub">
          Name a thesis. We bundle the contracts that price it into one diversified basket, and flag
          when the news moves faster than the odds.
        </p>
        <div className="ascii" aria-hidden="true">
          {TEXTURE.map((runs, i) => (
            <div key={i}>
              {runs.map(([cls, text], j) => (cls ? <span key={j} className={cls}>{text}</span> : text))}
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
