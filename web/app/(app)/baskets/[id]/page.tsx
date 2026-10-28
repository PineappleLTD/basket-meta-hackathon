"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
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
import { date, errorText, signedPct, usd } from "@/lib/format";

const QUICK_AMOUNTS = [50, 100, 500, 1000];
const DEFAULT_AMOUNT = 100;
// Wait for typing to pause before pricing the order.
const PREVIEW_DELAY_MS = 400;

const legKey = (marketId: number, side: string) => `${marketId}-${side}`;

export default function Page() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const { refresh } = useBaskets();
  const [basket, setBasket] = useState<Basket | null>(null);
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
    if (!basket || !confirm(`Delete "${basket.name}"?`)) return;
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

  const change = basket ? basket.nav / BASE_NAV - 1 : 0;
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

  return (
    <div className="pane">
      {error && (
        <p className="thesis-note auth-error" role="alert">
          {error}
        </p>
      )}
      {!basket && !error && <p className="thesis-note">Loading…</p>}

      {basket && (
        <>
          <header className="pane-head">
            <div className="eyebrow">basket</div>
            <h1 className="app-title">{basket.name}</h1>
          </header>
          {basket.description && <p className="picks-summary">{basket.description}</p>}
          <div className="picks-stats mono">
            <span>
              {basket.positions.length} markets
            </span>
            <span>
              since created{" "}
              <span className={change < 0 ? "down" : "up"}>{signedPct(change)}</span>
            </span>
            <span>created {date(basket.created_at)}</span>
          </div>

          {account && (
            <InvestPanel
              basket={basket}
              account={account}
              held={held}
              onPreview={setPreview}
              onTraded={traded}
              onUnauthorized={() => router.replace("/login")}
            />
          )}

          <h2 className="section-title">What&rsquo;s in it</h2>
          <PositionLegend />
          <PositionTable
            rows={basket.positions.map((p) => ({
              market: p.market,
              side: p.side,
              price: p.price,
              weight: p.target_weight,
              note: note(p.market.id, p.side),
            }))}
          />

          <button type="button" className="text-btn" onClick={remove} disabled={deleting}>
            {deleting ? "deleting…" : "delete basket"}
          </button>
        </>
      )}
    </div>
  );
}

interface InvestProps {
  basket: Basket;
  account: Account;
  held: Account["baskets"][number] | null;
  onPreview: (p: TradePreview | null) => void;
  onTraded: (a: Account) => void;
  onUnauthorized: () => void;
}

// Pick an amount, see what it buys (a dry run), then place it.
function InvestPanel({ basket, account, held, onPreview, onTraded, onUnauthorized }: InvestProps) {
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
    <section className="invest">
      <div className="invest-head">
        <h2 className="section-title">Invest</h2>
        <span className="mono invest-cash">
          {usd(account.cash)} available{account.mode === "paper" && " · paper money"}
        </span>
      </div>

      {held && (
        <div className="invest-held">
          <div>
            <span className="eyebrow">you own</span>
            <span className="mono">{usd(held.value)}</span>
          </div>
          <div>
            <span className="eyebrow">you paid</span>
            <span className="mono">{usd(held.cost)}</span>
          </div>
          <div>
            <span className="eyebrow">profit / loss</span>
            <span className={`mono ${held.unrealized_pnl < 0 ? "down" : "up"}`}>
              {held.unrealized_pnl >= 0 ? "+" : "−"}
              {usd(Math.abs(held.unrealized_pnl))}
            </span>
          </div>
          <button
            type="button"
            className="text-btn"
            disabled={busy}
            onClick={() =>
              confirm(`Sell everything you hold in "${basket.name}"?`) &&
              run(() => sellBasket(basket.id), "Sold. The cash is back in your account.")
            }
          >
            sell all
          </button>
        </div>
      )}

      <label htmlFor="amount" className="invest-label">
        How much {held ? "more " : ""}do you want to put in?
      </label>
      <div className="invest-row">
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
        {QUICK_AMOUNTS.filter((a) => a <= account.cash).map((a) => (
          <button
            key={a}
            type="button"
            className="bd-chip invest-chip"
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
            <p>
              Buys contracts in <b>{preview.orders.length}</b> of {basket.positions.length} markets for{" "}
              <b>{usd(spend)}</b> (incl. {usd(fees)} fees). You&rsquo;ll have {usd(preview.cash_after)} left.
            </p>
            {preview.skipped.length > 0 && (
              <p className="pt-skip">
                {preview.skipped.length} {preview.skipped.length === 1 ? "market is" : "markets are"} skipped: their
                share of {usd(amount)} is too small to buy a single contract. Put in more to include{" "}
                {preview.skipped.length === 1 ? "it" : "them"}.
              </p>
            )}
            <p className="invest-fine">
              Contracts are bought whole, so a few cents may stay as cash. Rows below show what each market gets.
            </p>
          </>
        )}
      </div>

      <button
        type="button"
        className="btn"
        disabled={busy || !preview || pricing || !!problem}
        onClick={() => run(() => buyBasket(basket.id, amount), `Bought. ${usd(spend)} invested.`)}
      >
        {busy ? "placing orders…" : preview ? `Buy for ${usd(spend)}` : "Buy"}
      </button>
    </section>
  );
}
