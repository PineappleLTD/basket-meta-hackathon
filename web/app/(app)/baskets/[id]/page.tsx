"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { BasketName } from "@/components/BasketName";
import { useBaskets } from "@/components/BasketsContext";
import { PositionLegend, PositionTable } from "@/components/PositionTable";
import { UnauthorizedError } from "@/lib/auth";
import {
  buyBasket,
  getAccount,
  previewBuy,
  sellBasket,
  type Account,
  type TradePreview,
} from "@/lib/accounts";
import { BASE_NAV, deleteBasket, getBasket, type Basket } from "@/lib/baskets";
import { date, errorText, pct, signedPct, usd } from "@/lib/format";

const QUICK_AMOUNTS = [50, 100, 500, 1000];
const DEFAULT_AMOUNT = 100;
// Wait for typing to pause before pricing the order.
const PREVIEW_DELAY_MS = 400;
// Categories past this many are folded into "Other".
const TOP_CATEGORIES = 5;

const legKey = (marketId: number, side: string) => `${marketId}-${side}`;

export default function Page() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const { refresh, baskets, titles } = useBaskets();
  const [basket, setBasket] = useState<Basket | null>(null);
  // The sidebar's copy has the latest name: a just-saved basket gets its title after it opens.
  const name = (basket && baskets?.find((b) => b.id === basket.id)?.name) ?? basket?.name ?? "";
  const [account, setAccount] = useState<Account | null>(null);
  const [preview, setPreview] = useState<TradePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  function fail(err: unknown) {
    if (err instanceof UnauthorizedError) router.replace("/login");
    else setError(errorText(err));
  }

  useEffect(() => {
    getBasket(id).then(setBasket).catch(fail);
    getAccount().then(setAccount).catch(fail);
  }, [id]);

  async function remove() {
    if (!basket || !confirm(`Delete "${name}"?`)) return;
    setDeleting(true);
    try {
      await deleteBasket(basket.id);
      await refresh();
      router.push("/basket");
    } catch (err) {
      setError(errorText(err));
      setDeleting(false);
    }
  }

  // A trade moves the basket's value too, so reload it alongside the account.
  async function traded(next: Account) {
    setAccount(next);
    setPreview(null);
    if (basket) setBasket(await getBasket(basket.id));
    refresh();
  }

  const held = account?.baskets.find((b) => b.basket_id === basket?.id) ?? null;

  const orders = new Map(preview?.orders.map((o) => [legKey(o.market_id, o.side), o]));
  const skipped = new Map(preview?.skipped.map((s) => [legKey(s.market_id, s.side), s]));
  const holdings = new Map(held?.holdings.map((h) => [legKey(h.market.id, h.side), h]));

  function note(marketId: number, side: string) {
    const key = legKey(marketId, side);
    const order = orders.get(key);
    const skip = skipped.get(key);
    const holding = holdings.get(key);
    const parts = [];
    if (order) parts.push(`buys ${order.count} for ${usd(-order.cash_delta)}`);
    if (skip) parts.push(<span className="pt-skip">skipped: too little to buy 1 contract</span>);
    if (holding) parts.push(`you hold ${holding.contracts}`);
    return parts.length ? parts.map((p, i) => <span key={i}>{p}</span>) : null;
  }

  if (!basket) {
    return (
      <div className="bd">
        <div className="bd-in">
          {error ? (
            <p className="thesis-note auth-error" role="alert">
              {error}
            </p>
          ) : (
            <p className="thesis-note">Loading…</p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="bd">
      <div className="bd-in">
        <header className="bd-head">
          <div className="bd-title">
            <div className="eyebrow">basket · created {date(basket.created_at)}</div>
            <h1 className="app-title">
              <BasketName name={name} state={titles[basket.id]} />
            </h1>
            {basket.description && <p className="bd-desc">{basket.description}</p>}
          </div>
          <button type="button" className="bd-ghost" onClick={remove} disabled={deleting}>
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5" />
            </svg>
            {deleting ? "Deleting…" : "Delete"}
          </button>
        </header>

        {error && (
          <p className="thesis-note auth-error" role="alert">
            {error}
          </p>
        )}

        <Kpis basket={basket} held={held} />

        <div className="bd-grid">
          <div className="bd-main">
            <section className="card">
              <div className="card-head">
                <h2>
                  Positions <span className="mono card-meta">{basket.positions.length}</span>
                </h2>
                <details className="bd-help">
                  <summary>How to read this</summary>
                  <PositionLegend />
                </details>
              </div>
              <PositionTable
                rows={basket.positions.map((p) => ({
                  market: p.market,
                  side: p.side,
                  price: p.price,
                  weight: p.target_weight,
                  note: note(p.market.id, p.side),
                }))}
              />
            </section>
          </div>

          <aside className="bd-side">
            {account ? (
              <InvestPanel
                basket={basket}
                name={name}
                account={account}
                held={held}
                onPreview={setPreview}
                onTraded={traded}
                onUnauthorized={() => router.replace("/login")}
              />
            ) : (
              !error && <section className="card card-pad thesis-note">Loading account…</section>
            )}
            <Allocation basket={basket} />
          </aside>
        </div>
      </div>
    </div>
  );
}

// Headline numbers across the top of the page.
function Kpis({ basket, held }: { basket: Basket; held: Account["baskets"][number] | null }) {
  const change = basket.nav / BASE_NAV - 1;
  const yes = basket.positions.filter((p) => p.side === "yes").length;
  const now = Date.now();
  const nextClose = basket.positions
    .map((p) => p.market.closes_at)
    .filter((c): c is string => !!c && new Date(c).getTime() > now)
    .sort()[0];

  return (
    <dl className="kpis">
      <div className="kpi">
        <dt>Index value</dt>
        <dd className="mono">{basket.nav.toFixed(2)}</dd>
        <span className="kpi-sub">
          <span className={change < 0 ? "down" : "up"}>{signedPct(change)}</span> since created
        </span>
      </div>
      <div className="kpi">
        <dt>Your stake</dt>
        <dd className="mono">{held ? usd(held.value) : "—"}</dd>
        <span className="kpi-sub">
          {held ? (
            <span className={held.unrealized_pnl < 0 ? "down" : "up"}>
              {held.unrealized_pnl >= 0 ? "+" : "−"}
              {usd(Math.abs(held.unrealized_pnl))} profit / loss
            </span>
          ) : (
            "Not invested yet"
          )}
        </span>
      </div>
      <div className="kpi">
        <dt>Markets</dt>
        <dd className="mono">{basket.positions.length}</dd>
        <span className="kpi-sub">
          {yes} yes · {basket.positions.length - yes} no
        </span>
      </div>
      <div className="kpi">
        <dt>Next close</dt>
        <dd className="mono">{nextClose ? date(nextClose) : "—"}</dd>
        <span className="kpi-sub">first market to resolve</span>
      </div>
    </dl>
  );
}

// Where the money goes: a bar across all positions, then the split by category.
function Allocation({ basket }: { basket: Basket }) {
  const byCategory = new Map<string, number>();
  for (const p of basket.positions) {
    const c = p.market.category || "Other";
    byCategory.set(c, (byCategory.get(c) ?? 0) + p.target_weight);
  }
  const sorted = [...byCategory].sort((a, b) => b[1] - a[1]);
  const rows = sorted.slice(0, TOP_CATEGORIES);
  const rest = sorted.slice(TOP_CATEGORIES).reduce((s, [, w]) => s + w, 0);
  if (rest > 0) rows.push(["Other", rest]);
  const yes = basket.positions.filter((p) => p.side === "yes").reduce((s, p) => s + p.target_weight, 0);

  return (
    <section className="card card-pad">
      <div className="card-head card-head-flush">
        <h2>Allocation</h2>
      </div>
      <div className="alloc" aria-hidden="true">
        {basket.positions.map((p) => (
          <i
            key={legKey(p.market.id, p.side)}
            className={p.side}
            style={{ flexGrow: p.target_weight }}
            title={`${p.market.question} · ${p.side} · ${pct(p.target_weight)}`}
          />
        ))}
      </div>
      <div className="bd-split mono">
        <span>
          <i className="yes" /> YES {pct(yes)}
        </span>
        <span>
          <i className="no" /> NO {pct(1 - yes)}
        </span>
      </div>
      <ul className="bd-cats">
        {rows.map(([c, w]) => (
          <li key={c}>
            <span className="bd-cat">{c}</span>
            <span className="mono">{pct(w)}</span>
            <div className="fl-track">
              <i style={{ width: pct(w) }} />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

interface InvestProps {
  basket: Basket;
  name: string;
  account: Account;
  held: Account["baskets"][number] | null;
  onPreview: (p: TradePreview | null) => void;
  onTraded: (a: Account) => void;
  onUnauthorized: () => void;
}

// Pick an amount, see what it buys (a dry run), then place it.
function InvestPanel({ basket, name, account, held, onPreview, onTraded, onUnauthorized }: InvestProps) {
  const [input, setInput] = useState(String(Math.min(DEFAULT_AMOUNT, Math.floor(account.cash))));
  const [preview, setPreview] = useState<TradePreview | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [pricing, setPricing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  const amount = Number(input);
  const valid = input.trim() !== "" && Number.isFinite(amount) && amount > 0;

  function show(p: TradePreview | null) {
    setPreview(p);
    onPreview(p);
  }

  useEffect(() => {
    show(null);
    setProblem(null);
    if (!valid) return;
    if (amount > account.cash) return setProblem(`You have ${usd(account.cash)} available.`);
    setPricing(true);
    let stale = false;
    const t = setTimeout(() => {
      previewBuy(basket.id, amount)
        .then((p) => !stale && show(p))
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
  }, [input, account.cash, basket.id]);

  async function run(action: () => Promise<{ account: Account }>, message: string) {
    setBusy(true);
    setProblem(null);
    try {
      onTraded((await action()).account);
      setDone(message);
    } catch (err) {
      if (err instanceof UnauthorizedError) return onUnauthorized();
      setProblem(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const spend = preview ? -preview.cash_delta : 0;
  const fees = preview?.orders.reduce((s, o) => s + o.fee, 0) ?? 0;

  return (
    <>
      {held && (
        <section className="card card-pad">
          <div className="card-head card-head-flush">
            <h2>Your position</h2>
            <button
              type="button"
              className="bd-ghost bd-ghost-sm"
              disabled={busy}
              onClick={() =>
                confirm(`Sell everything you hold in "${name}"?`) &&
                run(() => sellBasket(basket.id), "Sold. The cash is back in your account.")
              }
            >
              Sell all
            </button>
          </div>
          <dl className="ticket-rows">
            <div>
              <dt>Value</dt>
              <dd className="mono">{usd(held.value)}</dd>
            </div>
            <div>
              <dt>Cost</dt>
              <dd className="mono">{usd(held.cost)}</dd>
            </div>
            <div>
              <dt>Profit / loss</dt>
              <dd className={`mono ${held.unrealized_pnl < 0 ? "down" : "up"}`}>
                {held.unrealized_pnl >= 0 ? "+" : "−"}
                {usd(Math.abs(held.unrealized_pnl))}
              </dd>
            </div>
          </dl>
        </section>
      )}

      <section className="card card-pad ticket">
        <div className="card-head card-head-flush">
          <h2>{held ? "Add money" : "Invest"}</h2>
          {account.mode === "paper" && <span className="pill">paper</span>}
        </div>

        <label htmlFor="amount" className="invest-label">
          Amount <span className="mono">{usd(account.cash)} available</span>
        </label>
        <div className="invest-field">
          <span aria-hidden="true">$</span>
          <input
            id="amount"
            inputMode="decimal"
            value={input}
            onChange={(e) => {
              setDone(null);
              setInput(e.target.value.replace(/[^0-9.]/g, ""));
            }}
            autoComplete="off"
          />
        </div>
        <div className="invest-chips">
          {QUICK_AMOUNTS.filter((a) => a <= account.cash).map((a) => (
            <button
              key={a}
              type="button"
              className="invest-chip"
              aria-pressed={amount === a}
              onClick={() => {
                setDone(null);
                setInput(String(a));
              }}
            >
              {usd(a).replace(".00", "")}
            </button>
          ))}
        </div>

        <div className="invest-preview" aria-live="polite">
          {problem && <p className="auth-error">{problem}</p>}
          {done && <p className="invest-done">{done}</p>}
          {!problem && !done && pricing && <p className="thesis-note">Pricing your order…</p>}
          {!problem && !done && preview && !pricing && (
            <>
              <dl className="ticket-rows">
                <div>
                  <dt>Markets bought</dt>
                  <dd className="mono">
                    {preview.orders.length} of {basket.positions.length}
                  </dd>
                </div>
                <div>
                  <dt>Fees</dt>
                  <dd className="mono">{usd(fees)}</dd>
                </div>
                <div>
                  <dt>Cash after</dt>
                  <dd className="mono">{usd(preview.cash_after)}</dd>
                </div>
                <div className="ticket-total">
                  <dt>Total</dt>
                  <dd className="mono">{usd(spend)}</dd>
                </div>
              </dl>
              {preview.skipped.length > 0 && (
                <p className="pt-skip invest-fine">
                  {preview.skipped.length} {preview.skipped.length === 1 ? "market is" : "markets are"} skipped: their
                  share of {usd(amount)} is too small to buy a single contract. Put in more to include{" "}
                  {preview.skipped.length === 1 ? "it" : "them"}.
                </p>
              )}
            </>
          )}
        </div>

        <button
          type="button"
          className="btn ticket-btn"
          disabled={busy || !preview || pricing || !!problem}
          onClick={() => run(() => buyBasket(basket.id, amount), `Bought. ${usd(spend)} invested.`)}
        >
          {busy ? "Placing orders…" : preview ? `Buy for ${usd(spend)}` : "Buy"}
        </button>
        <p className="invest-fine">
          Contracts are bought whole, so a few cents may stay as cash. The positions list shows what each market gets.
        </p>
      </section>
    </>
  );
}
