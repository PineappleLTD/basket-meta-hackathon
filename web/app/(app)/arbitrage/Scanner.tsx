"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAccount } from "@/components/AccountContext";
import { useBaskets } from "@/components/BasketsContext";
import { InfoTip } from "@/components/InfoTip";
import { UnauthorizedError } from "@/lib/auth";
import {
  getArbitrage,
  KIND_LABEL,
  listLocks,
  placeLock,
  previewLock,
  rescan,
  type Arbitrage,
  type Lock,
  type LockPreview,
  type LockResult,
  type Opportunity,
} from "@/lib/arbitrage";
import { date, dateTime, errorText, pct, usd } from "@/lib/format";
import { usePoll } from "@/lib/poll";

// A scan runs after every sync; re-read the results this often.
const POLL_MS = 30_000;
const PREVIEW_DELAY_MS = 400;

/** A price to a tenth of a cent when it needs it: 42¢, 6.8¢. */
const price = (p: number) => {
  const c = Math.round(p * 1000) / 10;
  return `${Number.isInteger(c) ? c : c.toFixed(1)}¢`;
};

function ago(iso: string) {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours} h ago` : date(iso);
}

// Every group of Kalshi markets whose prices break a rule of probability, priced against the live
// order books; lock one into a basket that's held to settlement.
export function Scanner() {
  const router = useRouter();
  const { refresh: refreshBaskets } = useBaskets();
  const { setAccount } = useAccount();
  const [data, setData] = useState<Arbitrage | null>(null);
  const [locks, setLocks] = useState<Lock[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scanning, setScanning] = useState<"scan" | "sync" | null>(null);

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

  const s = data?.summary;
  const last = s?.last_scan;

  return (
    <div className="bd">
      <div className="bd-in arb">
        <header className="bd-head">
          <p className="bd-meta">Arbitrage scanner · Kalshi</p>
          <h1 className="bd-title">Prices that break the rules of probability</h1>
          <p className="bd-desc">
            Some Kalshi markets can&rsquo;t disagree without breaking logic: &ldquo;above 4%&rdquo; can never be likelier
            than &ldquo;above 3.75%&rdquo;, and the outcomes of one event can&rsquo;t add up to more than 100%. When
            their prices drift apart there&rsquo;s a set of contracts that pays out whatever happens. The scanner checks
            every group after each sync, prices the promising ones against the live order books with Kalshi&rsquo;s
            fees, and logs what still pays.
          </p>
        </header>

        {error && (
          <p className="bd-status auth-error" role="alert">
            {error}
          </p>
        )}

        {s && (
          <dl className="stats arb-stats">
            <div>
              <dt>Open now</dt>
              <dd>{s.active}</dd>
              <span className="arb-sub">{usd(s.profit_available)} lockable</span>
            </div>
            <div>
              <dt>Logged so far</dt>
              <dd>{s.opportunities_seen}</dd>
              <span className="arb-sub">{usd(s.best_profit_seen)} at their best</span>
            </div>
            <div>
              <dt>Scans</dt>
              <dd>{s.scans}</dd>
              <span className="arb-sub">{last ? `last ${ago(last.started_at)}` : "none yet"}</span>
            </div>
            <div>
              <dt>Your locked profit</dt>
              <dd className={s.locked_profit > 0 ? "up" : undefined}>{usd(s.locked_profit)}</dd>
              <span className="arb-sub">
                {s.locks} {s.locks === 1 ? "lock" : "locks"} · {usd(s.locked_payout)} guaranteed
              </span>
            </div>
          </dl>
        )}

        <div className="arb-actions">
          <button type="button" className="btn" onClick={() => scan(false)} disabled={!!scanning}>
            {scanning === "scan" ? "Scanning…" : "Rescan"}
          </button>
          <button type="button" className="text-btn" onClick={() => scan(true)} disabled={!!scanning}>
            {scanning === "sync" ? "Syncing Kalshi, then scanning… (about 30s)" : "Sync markets first"}
          </button>
          {last && !scanning && (
            <span className="arb-meta">
              {last.error
                ? `Last scan failed: ${last.error}`
                : `${last.groups.toLocaleString()} groups across ${last.markets.toLocaleString()} markets checked in ${(
                    last.duration_ms / 1000
                  ).toFixed(1)}s · ${last.candidates} priced live`}
            </span>
          )}
        </div>

        <section className="bd-section">
          <h2>
            Open now <span className="bd-count">{data?.opportunities.length ?? ""}</span>
          </h2>
          {!data && !error && <p className="bd-status">Loading…</p>}
          {data && data.opportunities.length === 0 && (
            <p className="bd-foot">
              Nothing open right now. That&rsquo;s normal: market makers keep these prices tight, and most gaps close
              within minutes. {s && s.opportunities_seen > 0 && `${s.opportunities_seen} have been logged so far. `}
              {!last && "Run a scan, or sync markets first if they haven't been synced since this feature was added."}
            </p>
          )}
          {data && data.opportunities.length > 0 && (
            <ul className="arb-list">
              {data.opportunities.map((o) => (
                <OpportunityCard key={o.id} opp={o} onLocked={locked} onUnauthorized={() => router.replace("/login")} />
              ))}
            </ul>
          )}
        </section>

        {locks && locks.length > 0 && (
          <section className="bd-section">
            <h2>
              Your locks <span className="bd-count">{locks.length}</span>
            </h2>
            <LocksTable locks={locks} />
          </section>
        )}

        <section className="bd-section">
          <h2>How it checks</h2>
          <ul className="arb-how">
            <li>
              <b>Strike ladders.</b> Within one event, &ldquo;above $150k&rdquo; implies &ldquo;above $120k&rdquo;.
              YES on the lower strike plus NO on the higher pays at least $1. Only markets whose rules differ in
              nothing but the strike count, so one event&rsquo;s unrelated goals never pair.
            </li>
            <li>
              <b>Brackets.</b> At most one outcome of a mutually exclusive event wins, so NO on k of them pays at
              least k&nbsp;&minus;&nbsp;1. When the ranges provably cover every result, YES on all of them pays exactly
              $1.
            </li>
            <li>
              <b>What &ldquo;guaranteed&rdquo; means.</b> The payout floor holds once every leg is held. A lock
              re-prices against the live books first and buys every leg in full, or nothing.
            </li>
          </ul>
        </section>
      </div>
    </div>
  );
}

function OpportunityCard({
  opp,
  onLocked,
  onUnauthorized,
}: {
  opp: Opportunity;
  onLocked: (r: LockResult) => void;
  onUnauthorized: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <li className="arb-card">
      <div className="arb-tags">
        <span className="tag">{KIND_LABEL[opp.kind]}</span>
        <span className="tag">{opp.confirmed ? "live order books" : "depth unknown"}</span>
      </div>
      <h3 className="arb-title">{opp.title}</h3>
      <p className="arb-why">{opp.explanation}</p>

      <table className="pt arb-legs">
        <thead>
          <tr>
            <th scope="col">Buy</th>
            <th scope="col" className="num">
              Best
            </th>
            {opp.sets && (
              <th scope="col" className="num">
                Avg for {opp.sets}
                <InfoTip end>Average price per contract walking the order book for the whole size.</InfoTip>
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
              <td className="num">{price(leg.best_price)}</td>
              {opp.sets && <td className="num">{leg.avg_price != null ? price(leg.avg_price) : "–"}</td>}
            </tr>
          ))}
        </tbody>
      </table>

      <dl className="ticket-rows arb-nums">
        {opp.sets != null && opp.cost != null && opp.payout != null && opp.profit != null ? (
          <>
            <div>
              <dt>{opp.sets.toLocaleString()} sets cost, with fees</dt>
              <dd>{usd(opp.cost)}</dd>
            </div>
            <div>
              <dt>Pay at least</dt>
              <dd>{usd(opp.payout)}</dd>
            </div>
            <div className="ticket-total">
              <dt>Locked profit</dt>
              <dd className="up">
                {usd(opp.profit)}
                {opp.return_pct != null && ` · ${pct(opp.return_pct, 1)}`}
              </dd>
            </div>
          </>
        ) : (
          <div>
            <dt>Per set, after fees</dt>
            <dd className={opp.edge_per_set > 0 ? "up" : undefined}>{price(opp.edge_per_set)}</dd>
          </div>
        )}
        {opp.annualized != null && (
          <div>
            <dt>
              Yearly rate
              <InfoTip end>Simple, not compounded: the return spread over the time until the last leg closes.</InfoTip>
            </dt>
            <dd>{pct(opp.annualized, 1)}</dd>
          </div>
        )}
        {opp.closes_at && (
          <div>
            <dt>Money tied up until</dt>
            <dd>{date(opp.closes_at)}</dd>
          </div>
        )}
      </dl>

      <div className="arb-foot">
        <span className="arb-meta">
          Seen {opp.times_seen} {opp.times_seen === 1 ? "time" : "times"} since {dateTime(opp.first_seen)}
        </span>
        <button type="button" className="btn arb-lock" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? "Close" : "Lock it"}
        </button>
      </div>
      {open && <LockTicket opp={opp} onLocked={onLocked} onUnauthorized={onUnauthorized} />}
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
        Paper money. Every leg fills in full or nothing locks. The lock basket is held to settlement: no rebalancing or automations.
      </p>
    </div>
  );
}

const STATUS: Record<string, string> = { locked: "Held", settled: "Paid out", closed: "Sold" };

function LocksTable({ locks }: { locks: Lock[] }) {
  return (
    <table className="pt">
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
