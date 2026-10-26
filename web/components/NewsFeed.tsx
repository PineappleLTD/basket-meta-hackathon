import { REBALANCE_THRESHOLD } from "@/lib/data";
import { ago, cents } from "@/lib/format";
import type { NewsSignal } from "@/lib/types";
import { SectionHeader } from "./SectionHeader";

function NewsItem({ n }: { n: NewsSignal }) {
  return (
    <li className="item">
      <div className="when">{ago(n.minutesAgo)}</div>
      <div>
        <div className="hl">{n.headline}</div>
        <div className="src">
          {n.source} · <span className="mono">{n.ticker}</span> · {n.question}
        </div>
      </div>
      <div className="mv">
        <span>
          <i>Pred</i>
          {cents(n.predicted)}
        </span>
        <span>
          <i>Actual</i>
          {cents(n.actual)}
        </span>
        {n.rebalance ? <span className="tag">Rebalance</span> : <span className="tag off">In line</span>}
      </div>
    </li>
  );
}

export function NewsFeed({ news }: { news: NewsSignal[] }) {
  const threshold = Math.round(REBALANCE_THRESHOLD * 100);
  return (
    <section id="signals">
      <SectionHeader
        num="03"
        label="News"
        title="Signals, not noise"
        lede={`Each headline gets a predicted move on the contract it touches. When the market moves ${threshold}¢ more or less than predicted, we flag a rebalance.`}
      />
      <ul className="news">
        {news.length ? news.map((n) => <NewsItem key={n.headline} n={n} />) : (
          <li className="empty">No recent headlines touch this basket.</li>
        )}
      </ul>
    </section>
  );
}
