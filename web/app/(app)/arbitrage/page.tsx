"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";
import { useAccount } from "@/components/AccountContext";
import { useBaskets } from "@/components/BasketsContext";
import { UnauthorizedError } from "@/lib/auth";
import {
  findImplications,
  getArbitrage,
  KIND_LABEL,
  listLocks,
  placeLock,
  previewLock,
  rescan,
  type ArbKind,
  type Arbitrage,
  type Implications,
  type Lock,
  type LockPreview,
  type LockResult,
  type Opportunity,
} from "@/lib/arbitrage";
import { date, errorText, pct, usd } from "@/lib/format";
import { usePoll } from "@/lib/poll";

// A scan runs after every sync; re-read the results this often.
const POLL_MS = 30_000;
const PREVIEW_DELAY_MS = 400;
// A scan older than this no longer counts as "live".
const LIVE_FOR_MS = 60 * 60_000;

/** A 0..1 price as Kalshi quotes it: 0.58. */
const quote = (p: number) => p.toFixed(2);

/** "38s ago", "2m ago", "3h ago", else the date. */
function ago(iso: string) {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours}h ago` : date(iso);
}

const TAG: Record<ArbKind, string> = {
  ladder: "Strike ladder",
  time: "Deadline pair",
  bracket_no: "Bracket",
  bracket_yes: "Bracket",
  implication: "Model pair",
};

const isBracket = (o: Opportunity) => o.kind === "bracket_yes" || o.kind === "bracket_no";

/** Cost and fees for one set: averages over the sized fill when there is one, else the best prices. */
function perSet(o: Opportunity) {
  if (o.sets && o.cost != null && o.legs.every((l) => l.avg_price != null)) {
    const cost = o.legs.reduce((s, l) => s + (l.avg_price ?? 0), 0);
    return { cost, fees: Math.max(0, o.cost / o.sets - cost) };
  }
  const cost = o.legs.reduce((s, l) => s + l.best_price, 0);
  return { cost, fees: Math.max(0, o.payout_per_set - cost - o.edge_per_set) };
}

/** What the set pays, in the words of its structure. */
function payoutText(o: Opportunity) {
  const floor = usd(o.payout_per_set);
  switch (o.kind) {
    case "ladder":
      return `pays ≥ ${floor} wherever it lands`;
    case "time":
      return `pays ≥ ${floor} whether or not it happens`;
    case "bracket_no":
      return `at most one wins, pays ≥ ${floor}`;
    case "bracket_yes":
      return `exactly one range wins, pays ${floor}`;
    default:
      return `pays ≥ ${floor} only if the link holds`;
  }
}

// Every group of Kalshi markets whose prices break a rule of probability, priced against the live
// order books; lock one into a basket that's held to settlement.
export default function Page() {
  const router = useRouter();
  const { refresh: refreshBaskets } = useBaskets();
  const { setAccount } = useAccount();
  const [data, setData] = useState<Arbitrage | null>(null);
  const [locks, setLocks] = useState<Lock[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scanning, setScanning] = useState<"scan" | "sync" | null>(null);
  // Re-render the "38s ago" labels between polls.
  const [, tick] = useState(0);

  function fail(err: unknown) {
    if (err instanceof UnauthorizedError) router.replace("/login");
    else setError(errorText(err));
  }

  const load = () =>
    getArbitrage()
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch(fail);
  // Extra: without the account's locks the page still works.
  const loadLocks = () => listLocks().then(setLocks).catch(() => setLocks([]));

  useEffect(() => {
    load();
    loadLocks();
  }, []);
  usePoll(() => !scanning && load(), POLL_MS);
  usePoll(() => tick((n) => n + 1), 5_000);

  async function scan(sync: boolean) {
    setScanning(sync ? "sync" : "scan");
    setError(null);
    try {
      setData(await rescan(sync));
    } catch (err) {
      fail(err);
    } finally {
      setScanning(null);
    }
  }

  function locked(result: LockResult) {
    setAccount(result.account);
    refreshBaskets();
    loadLocks();
    load();
  }

  const toLogin = () => router.replace("/login");
  const s = data?.summary;
  const last = s?.last_scan;
  const live = !!last && !last.error && Date.now() - new Date(last.started_at).getTime() < LIVE_FOR_MS;
  const ladder = data?.opportunities.find((o) => o.kind === "ladder" && o.legs.length === 2) ?? null;

  return (
    <div className="bd">
      <div className="bd-in arb">
        <header className="arb-hero">
          <div>
            <p className="arb-eyebrow">Kalshi · live order books</p>
            <h1 className="arb-h1">Arbitrage scanner</h1>
            <p className="arb-lede">
              Linked Kalshi markets priced against the rules of probability. Each set below pays out whatever happens,
              after fees.
            </p>
          </div>
          <LadderCard opp={ladder} scanning={!!scanning} />
        </header>

        {error && (
          <p className="bd-status auth-error" role="alert">
            {error}
          </p>
        )}

        <section className="arb-board" aria-label="Scanner status">
          <div className="arb-bar">
            <p className="arb-status" aria-live="polite">
              <i className={last?.error ? "bad" : live ? "on" : undefined} aria-hidden />
              {scanning === "sync"
                ? "Syncing Kalshi, then scanning · about 30s"
                : scanning
                  ? "Scanning…"
                  : last?.error
                    ? `Last scan failed · ${last.error}`
                    : last
                      ? `${live ? "Live" : "Idle"} · last scan ${ago(last.started_at)}`
                      : "No scans yet · sync markets to start"}
            </p>
            <div className="arb-bar-btns">
              <button type="button" className="arb-btn-ghost" onClick={() => scan(true)} disabled={!!scanning}>
                Sync markets
              </button>
              <button type="button" className="arb-btn-accent" onClick={() => scan(false)} disabled={!!scanning}>
                <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden className={scanning ? "spin" : undefined}>
                  <path
                    d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2.5v3h-3"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
                Rescan
              </button>
            </div>
          </div>
          <dl className="arb-kpis">
            <div>
              <dt>Open now</dt>
              <dd>{s ? s.active : "–"}</dd>
              <span>{s ? `${usd(s.profit_available)} lockable` : " "}</span>
            </div>
            <div>
              <dt>Logged so far</dt>
              <dd>{s ? s.opportunities_seen.toLocaleString() : "–"}</dd>
              <span>{s ? `${usd(s.best_profit_seen)} at their best` : " "}</span>
            </div>
            <div>
              <dt>Scans</dt>
              <dd>{s ? s.scans.toLocaleString() : "–"}</dd>
              <span>
                {last && !last.error
                  ? `${last.groups.toLocaleString()} groups each · ${(last.duration_ms / 1000).toFixed(1)}s`
                  : "none yet"}
              </span>
            </div>
            <div>
              <dt>Your locked profit</dt>
              <dd>{s ? usd(s.locked_profit) : "–"}</dd>
              <span>
                {s
                  ? `${s.locks} ${s.locks === 1 ? "lock" : "locks"} · ${usd(s.locked_payout)} guaranteed at payout`
                  : " "}
              </span>
            </div>
          </dl>
        </section>

        <section className="arb-sec">
          <div className="arb-sec-head">
            <h2>
              Open now <span>{data?.opportunities.length ?? ""}</span>
            </h2>
            <p>Priced against live books, after Kalshi fees</p>
          </div>
          {!data && !error && <p className="arb-empty">Loading…</p>}
          {data && data.opportunities.length === 0 && (
            <p className="arb-empty">
              Nothing open right now. That&rsquo;s normal: market makers keep these prices tight, and most gaps close
              within minutes. {s && s.opportunities_seen > 0 && `${s.opportunities_seen} have been logged so far. `}
              {!last && "Sync markets to run the first scan."}
            </p>
          )}
          {data && data.opportunities.length > 0 && (
            <ul className="arb-rows">
              {data.opportunities.map((o) => (
                <OpportunityRow key={o.id} opp={o} onLocked={locked} onUnauthorized={toLogin} />
              ))}
            </ul>
          )}
        </section>

        {locks && locks.length > 0 && (
          <section className="arb-sec">
            <div className="arb-sec-head">
              <h2>
                Your locks <span>{locks.length}</span>
              </h2>
              <p>Paper money, held to settlement</p>
            </div>
            <LocksTable locks={locks} />
          </section>
        )}

        <section className="arb-sec arb-pairs-sec">
          <div className="arb-pairs-intro">
            <h2 className="arb-h2">Pairs across events.</h2>
            <span className="arb-warn">
              <svg viewBox="0 0 16 16" width="11" height="11" aria-hidden>
                <path d="M8 1.8 15 14H1z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
                <path d="M8 6.2v3.6M8 11.6v.4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
              </svg>
              Not guaranteed
            </span>
            <p>
              Links between different events, like &ldquo;a 2027 recession&rdquo; and &ldquo;the Fed cuts&rdquo;, need a
              model to read the questions. If one really implies the other, the implied market can&rsquo;t be the less
              likely one. These pay only if the link holds, so they&rsquo;re never called arbitrage.
            </p>
          </div>
          <ImplicationFinder onFound={load} onUnauthorized={toLogin} />
          {data && data.implications.length > 0 && (
            <ul className="arb-rows arb-pairs-list">
              {data.implications.map((o) => (
                <OpportunityRow key={o.id} opp={o} onLocked={locked} onUnauthorized={toLogin} />
              ))}
            </ul>
          )}
        </section>

        <section className="arb-sec">
          <h2 className="arb-h2">How it checks.</h2>
          <ol className="arb-how">
            <li>
              <div className="arb-art art-ladder" aria-hidden>
                <i style={{ height: 52 }} />
                <i style={{ height: 40 }} />
                <i className="hot" style={{ height: 45 }} />
                <i style={{ height: 17 }} />
              </div>
              <span className="arb-n">01</span>
              <h3>Strike ladders</h3>
              <p>
                Within one event, &ldquo;above $150k&rdquo; implies &ldquo;above $120k&rdquo;. Only markets whose rules
                differ in nothing but the strike are paired.
              </p>
              <code>YES low + NO high ≥ $1</code>
            </li>
            <li>
              <div className="arb-art art-time" aria-hidden>
                <span className="jun">Jun</span>
                <span className="dec">Dec</span>
                <i className="hot" />
                <i />
              </div>
              <span className="arb-n">02</span>
              <h3>Deadline pairs</h3>
              <p>
                &ldquo;By June&rdquo; implies &ldquo;by December&rdquo;. Only markets that close early when the event
                happens, with matching rules and windows.
              </p>
              <code>YES later + NO earlier ≥ $1</code>
            </li>
            <li>
              <div className="arb-art art-bracket" aria-hidden>
                <i />
                <i />
                <i className="hot" />
                <i />
                <i />
              </div>
              <span className="arb-n">03</span>
              <h3>Brackets</h3>
              <p>
                At most one outcome of a mutually exclusive event wins. If the ranges provably cover every result, YES on
                all of them pays exactly $1.
              </p>
              <code>NO on k brackets ≥ $(k − 1)</code>
            </li>
            <li>
              <div className="arb-art art-check" aria-hidden>
                <i />
                <span>+</span>
                <i />
                <span>=</span>
                <b>
                  <svg viewBox="0 0 16 16" width="12" height="12">
                    <path
                      d="m4 8.4 2.6 2.4L12 5.4"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.8"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </b>
              </div>
              <span className="arb-n">04</span>
              <h3>What &ldquo;guaranteed&rdquo; means</h3>
              <p>
                The payout floor holds once every leg is held. A lock re-prices against live books, buys every leg or
                nothing, and sells back any leg that overfills.
              </p>
              <code>all legs or none</code>
            </li>
          </ol>
        </section>
      </div>
    </div>
  );
}

// The hero's ladder: the top open ladder's two strikes when there is one, otherwise an illustration of the rule.
const EXAMPLE = [
  { label: "above 3.50%", p: 0.81 },
  { label: "above 3.75%", p: 0.64 },
  { label: "above 4.00%", p: 0.52 },
  { label: "above 4.25%", p: 0.21 },
];

function LadderCard({ opp, scanning }: { opp: Opportunity | null; scanning: boolean }) {
  // Legs are YES on the broader strike and NO on the narrower; a NO at p is the narrower's YES at 1 − p.
  const rows = opp
    ? opp.legs.map((l) => ({
        label: l.outcome ?? l.question,
        p: l.side === "yes" ? l.best_price : 1 - l.best_price,
        hot: l.side === "no",
      }))
    : EXAMPLE.map((r) => ({ ...r, hot: false }));
  return (
    <figure className="arb-ladder">
      <figcaption className="arb-ladder-head">
        <span>{opp ? opp.title : "Fed funds upper bound · example"}</span>
        <b className={scanning ? "pulse" : undefined}>{scanning ? "Scanning" : opp ? "Mispriced" : "Example"}</b>
      </figcaption>
      <ul>
        {rows.map((r) => (
          <li key={r.label} className={r.hot ? "hot" : undefined}>
            <span>{r.label}</span>
            <i>
              <i style={{ width: `${Math.max(2, Math.min(100, r.p * 100))}%` }} />
            </i>
            <span>{pct(r.p)}</span>
          </li>
        ))}
      </ul>
      <p>
        {opp
          ? "The narrower strike is priced likelier than the broader one, which can't happen."
          : "Each strike should be less likely than the one above."}
      </p>
    </figure>
  );
}

/** Leg chips: each leg, or one chip for a long bracket whose legs are all on the same side. */
function LegChips({ opp }: { opp: Opportunity }) {
  const legs = opp.legs;
  if (legs.length > 3 && legs.every((l) => l.side === legs[0].side)) {
    const total = legs.reduce((s, l) => s + l.best_price, 0);
    return (
      <div className="arb-chips">
        <span className="arb-chip">
          <b className={legs[0].side}>{legs[0].side === "yes" ? "YES" : "NO"}</b>{" "}
          <span>
            {opp.kind === "bracket_yes" ? "all" : "on"} {legs.length} {opp.kind === "bracket_yes" ? "ranges" : "outcomes"}
          </span>{" "}
          <em>{quote(total)}</em>
        </span>
      </div>
    );
  }
  return (
    <div className="arb-chips">
      {legs.map((l, i) => (
        <span key={`${l.market_id}-${l.side}`} className="arb-chip-wrap">
          {i > 0 && <span className="arb-plus">+</span>}
          <span className="arb-chip" title={l.question}>
            <b className={l.side}>{l.side === "yes" ? "YES" : "NO"}</b> <span>{l.outcome ?? l.question}</span>{" "}
            <em>{quote(l.best_price)}</em>
          </span>
        </span>
      ))}
    </div>
  );
}

function OpportunityRow({
  opp,
  onLocked,
  onUnauthorized,
}: {
  opp: Opportunity;
  onLocked: (r: LockResult) => void;
  onUnauthorized: () => void;
}) {
  const [open, setOpen] = useState(false);
  const one = perSet(opp);
  const sets = opp.sets;
  const profit = opp.profit;
  return (
    <li className={`arb-row${open ? " open" : ""}`}>
      <div className="arb-row-main">
        <div className="arb-row-what">
          <p className="arb-row-tags">
            <span className="arb-kind">{TAG[opp.kind]}</span>
            {!opp.confirmed && opp.guaranteed && <span className="arb-kind">Depth unknown</span>}
            <span>
              found {ago(opp.first_seen)}
              {opp.times_seen > 1 && ` · in ${opp.times_seen.toLocaleString()} scans`}
            </span>
          </p>
          <h3>
            {opp.title}
            {isBracket(opp) && (
              <span className="arb-row-count">
                {" "}
                · {opp.legs.length} {opp.kind === "bracket_yes" ? "ranges" : "outcomes"}
              </span>
            )}
          </h3>
          <LegChips opp={opp} />
          <p className="arb-row-line">
            cost {usd(one.cost)} + {usd(one.fees)} fees · {payoutText(opp)}
            {opp.closes_at && ` · settles by ${date(opp.closes_at)}`}
          </p>
        </div>
        <div className="arb-row-take">
          <strong>{sets && profit != null ? usd(profit) : usd(opp.edge_per_set)}</strong>
          <span>
            {sets && profit != null
              ? `+${usd(profit / sets)} × ${sets.toLocaleString()} sets`
              : opp.guaranteed
                ? "a set · depth unknown"
                : "a set, if the link holds"}
          </span>
          {opp.annualized != null && <span>{pct(opp.annualized, 1)} a year</span>}
        </div>
        <div className="arb-row-act">
          {opp.guaranteed ? (
            <button type="button" className="arb-btn-ink" onClick={() => setOpen(!open)} aria-expanded={open}>
              {open ? "Close" : "Lock all legs"}
            </button>
          ) : (
            <span className="arb-row-note">Not lockable</span>
          )}
        </div>
      </div>
      {open && (
        <div className="arb-row-more">
          <div>
            <p className="arb-why">{opp.explanation}</p>
            <table className="pt arb-legs">
              <thead>
                <tr>
                  <th scope="col">Leg</th>
                  <th scope="col" className="num">
                    Best
                  </th>
                  {sets && (
                    <th scope="col" className="num">
                      Avg for {sets.toLocaleString()}
                    </th>
                  )}
                </tr>
              </thead>
              <tbody>
                {opp.legs.map((leg) => (
                  <tr key={`${leg.market_id}-${leg.side}`}>
                    <td>
                      <span className={`side ${leg.side}`}>{leg.side === "yes" ? "YES" : "NO"}</span>{" "}
                      {leg.outcome ?? leg.question}
                      {leg.outcome && <span className="arb-q">{leg.question}</span>}
                    </td>
                    <td className="num">{quote(leg.best_price)}</td>
                    {sets && <td className="num">{leg.avg_price != null ? quote(leg.avg_price) : "–"}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <LockTicket opp={opp} onLocked={onLocked} onUnauthorized={onUnauthorized} />
        </div>
      )}
    </li>
  );
}

function LockTicket({
  opp,
  onLocked,
  onUnauthorized,
}: {
  opp: Opportunity;
  onLocked: (r: LockResult) => void;
  onUnauthorized: () => void;
}) {
  const [sets, setSets] = useState(String(opp.sets ?? 1));
  const [preview, setPreview] = useState<LockPreview | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [pricing, setPricing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<LockResult | null>(null);
  const n = Number(sets) >= 1 ? Math.floor(Number(sets)) : null;

  // Re-price against the live books whenever the size settles: the gap may have moved since the scan.
  useEffect(() => {
    setPreview(null);
    setProblem(null);
    if (!n) return;
    setPricing(true);
    let stale = false;
    const t = setTimeout(() => {
      previewLock(opp.id, n)
        .then((p) => !stale && setPreview(p))
        .catch((err) => {
          if (stale) return;
          if (err instanceof UnauthorizedError) onUnauthorized();
          else setProblem(errorText(err));
        })
        .finally(() => !stale && setPricing(false));
    }, PREVIEW_DELAY_MS);
    return () => {
      stale = true;
      clearTimeout(t);
    };
  }, [n, opp.id]);

  async function place() {
    if (!n || !preview) return;
    setBusy(true);
    setProblem(null);
    try {
      const result = await placeLock(opp.id, n);
      setDone(result);
      onLocked(result);
    } catch (err) {
      if (err instanceof UnauthorizedError) return onUnauthorized();
      setProblem(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="ticket arb-ticket" role="status">
        <p className="invest-done">
          Locked {done.filled_sets.toLocaleString()} sets: {usd(done.lock.payout)} guaranteed for{" "}
          {usd(done.lock.cost)}.
        </p>
        {done.note && <p className="invest-fine">{done.note}.</p>}
        <p className="invest-fine">
          <Link className="text-link" href={`/baskets/${done.lock.basket_id}`}>
            Open the lock basket →
          </Link>
        </p>
      </div>
    );
  }

  return (
    <div className="ticket arb-ticket">
      <label className="invest-label" htmlFor={`sets-${opp.id}`}>
        Sets
        <span>one contract of every leg each</span>
      </label>
      <div className="invest-field">
        <input
          id={`sets-${opp.id}`}
          inputMode="numeric"
          value={sets}
          onChange={(e) => setSets(e.target.value.replace(/[^\d]/g, ""))}
          aria-describedby={`sets-hint-${opp.id}`}
        />
      </div>
      <p className="invest-fine" id={`sets-hint-${opp.id}`}>
        {opp.sets ? `${opp.sets.toLocaleString()} made the most at the last scan's books.` : "Depth unknown until priced."}
      </p>
      <div className="invest-preview" aria-live="polite">
        {pricing && <p className="invest-fine">Pricing against the live order books…</p>}
        {problem && <p className="auth-error">{problem}</p>}
        {preview && (
          <dl className="ticket-rows">
            <div>
              <dt>Cost, with {usd(preview.fees)} fees</dt>
              <dd>{usd(preview.cost)}</dd>
            </div>
            <div>
              <dt>Pays at least</dt>
              <dd>{usd(preview.payout)}</dd>
            </div>
            <div>
              <dt>Cash after</dt>
              <dd>{usd(preview.cash_after)}</dd>
            </div>
            <div className="ticket-total">
              <dt>Locked profit</dt>
              <dd className="up">
                {usd(preview.profit)} · {pct(preview.return_pct, 1)}
              </dd>
            </div>
          </dl>
        )}
      </div>
      <button type="button" className="btn ticket-btn" onClick={place} disabled={!preview || busy}>
        {busy ? "Locking…" : preview ? `Lock ${preview.sets.toLocaleString()} sets for ${usd(preview.cost)}` : "Lock"}
      </button>
      <p className="invest-fine">
        Paper money. Every leg fills or nothing locks; if a leg fills short, the others&rsquo; extra contracts are sold
        back so every leg holds the same count. The lock basket is held to settlement: no rebalancing or automations.
      </p>
    </div>
  );
}

function ImplicationFinder({ onFound, onUnauthorized }: { onFound: () => void; onUnauthorized: () => void }) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<Implications | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (query.trim().length < 3) return;
    setBusy(true);
    setProblem(null);
    try {
      setResult(await findImplications(query.trim()));
      onFound();
    } catch (err) {
      if (err instanceof UnauthorizedError) return onUnauthorized();
      setProblem(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="arb-find">
      <form className="arb-form" onSubmit={submit}>
        <input
          className="arb-input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="A topic, e.g. fed rate cuts recession"
          aria-label="Topic to check for implications"
        />
        <button type="submit" className="arb-btn-ink arb-btn-tall" disabled={busy || query.trim().length < 3}>
          {busy ? "Asking…" : "Find pairs"}
        </button>
      </form>
      <p className="arb-hint">Checked on demand. Results show the model&rsquo;s link and the price gap.</p>
      {problem && <p className="auth-error">{problem}</p>}
      {result && (
        <>
          <p className="arb-hint">
            {result.pairs.length} {result.pairs.length === 1 ? "pair" : "pairs"} among {result.candidates_considered}{" "}
            markets about &ldquo;{result.query}&rdquo;.
          </p>
          {result.pairs.length > 0 && (
            <ul className="arb-found">
              {result.pairs.map((p) => (
                <li key={`${p.if_market.id}-${p.then_market.id}`}>
                  <p className="arb-pair">
                    If <b>{p.if_market.question}</b> then <b>{p.then_market.question}</b>
                  </p>
                  <p className="arb-hint">
                    <span className="arb-kind">{p.relation}</span> {pct(p.confidence)} confidence · {p.reason}
                  </p>
                  <p className={`arb-hint${p.mispriced ? " hot" : ""}`}>
                    {p.edge_per_set == null
                      ? "No quote on one side"
                      : p.mispriced
                        ? `Priced backwards: YES on the second plus NO on the first leaves ${usd(p.edge_per_set)} a set after fees, if the link holds`
                        : "Priced consistently with the link"}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

const STATUS: Record<string, string> = { locked: "Held", settled: "Paid out", closed: "Sold" };

function LocksTable({ locks }: { locks: Lock[] }) {
  return (
    <table className="pt arb-locks">
      <thead>
        <tr>
          <th scope="col">Lock</th>
          <th scope="col" className="num">
            Sets held
          </th>
          <th scope="col" className="num">
            Paid
          </th>
          <th scope="col" className="num">
            Pays at least
          </th>
          <th scope="col" className="num">
            Profit
          </th>
        </tr>
      </thead>
      <tbody>
        {locks.map((l) => (
          <tr key={l.basket_id}>
            <td>
              <Link className="pt-q text-link" href={`/baskets/${l.basket_id}`}>
                {l.title}
              </Link>
              <p className="pt-sub">
                {KIND_LABEL[l.kind]} · {STATUS[l.status ?? "locked"]}
                {l.closes_at && ` · settles by ${date(l.closes_at)}`}
              </p>
            </td>
            <td className="num">{(l.held_sets ?? 0).toLocaleString()}</td>
            <td className="num">{usd(l.cost)}</td>
            <td className="num">{usd(l.payout)}</td>
            {/* A lock sold before settlement gave up its floor; its result is in the basket's activity. */}
            <td className={`num ${l.status === "closed" ? "down" : "up"}`}>
              {l.status === "closed" ? "sold" : usd(l.profit)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
