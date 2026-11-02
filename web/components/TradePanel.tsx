"use client";

import { useEffect, useState } from "react";
import { UnauthorizedError } from "@/lib/auth";
import {
  buyBasket,
  previewBuy,
  previewRebalance,
  previewSell,
  rebalanceHoldings,
  sellBasket,
  type Account,
  type BasketHoldings,
  type SkippedLeg,
  type TradePreview,
} from "@/lib/accounts";
import type { Basket } from "@/lib/baskets";
import { errorText, pct, signedPct, signedUsd, usd } from "@/lib/format";
import { InfoTip } from "./InfoTip";

const QUICK_AMOUNTS = [50, 100, 500, 1000];
const SELL_FRACTIONS = [0.25, 0.5, 0.75, 1];
const DEFAULT_AMOUNT = 100;
// Wait for typing to pause before pricing the order.
const PREVIEW_DELAY_MS = 400;
// Holdings drifted further than this from target get a nudge to rebalance.
export const DRIFT_ALERT = 0.05;
// Below this, walking the book isn't worth a line in the preview.
const SHOWN_IMPACT = 0.005;

type Tab = "buy" | "sell" | "rebalance";

interface Props {
  basket: Basket;
  account: Account;
  held: BasketHoldings | null;
  // Names a market in messages, e.g. why a leg was skipped.
  question: (marketId: number) => string;
  onPreview: (p: TradePreview | null) => void;
  onTraded: (a: Account) => void;
  onUnauthorized: () => void;
}

// Prices an order (a dry run) whenever `key` changes, after typing pauses. `load` null clears it.
function usePreview(
  load: (() => Promise<TradePreview>) | null,
  key: string,
  onPreview: (p: TradePreview | null) => void,
  onUnauthorized: () => void,
) {
  const [preview, setPreview] = useState<TradePreview | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [pricing, setPricing] = useState(false);

  function show(p: TradePreview | null) {
    setPreview(p);
    onPreview(p);
  }

  useEffect(() => {
    show(null);
    setProblem(null);
    if (!load) return;
    setPricing(true);
    let stale = false;
    const t = setTimeout(() => {
      load()
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
  }, [key]);

  return { preview, problem, pricing, setProblem };
}

// Your holding, then buy, sell or rebalance it: pick an amount, see what it places (a dry run), and place it.
export function TradePanel({ basket, account, held, question, onPreview, onTraded, onUnauthorized }: Props) {
  const [tab, setTab] = useState<Tab>("buy");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const active: Tab = held ? tab : "buy";

  function pick(next: Tab) {
    setTab(next);
    setDone(null);
    setFailed(null);
  }

  async function run(action: () => Promise<{ account: Account; skipped: SkippedLeg[] }>, message: string) {
    setBusy(true);
    setFailed(null);
    try {
      const result = await action();
      onTraded(result.account);
      const skipped = result.skipped.length ? ` ${result.skipped.length} ${plural(result.skipped.length, "leg")} skipped.` : "";
      setDone(message + skipped);
    } catch (err) {
      if (err instanceof UnauthorizedError) return onUnauthorized();
      setFailed(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const shared = { basket, account, held, question, busy, done, failed, run, onPreview, onUnauthorized, clear: () => setDone(null) };

  return (
    <div className="ticket">
      {held && <HoldingSummary held={held} onRebalance={() => pick("rebalance")} />}

      <div className="ticket-head">
        <div className="ticket-tabs" role="tablist" aria-label="Order type">
          {(["buy", "sell", "rebalance"] as Tab[]).map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              className="ticket-tab"
              aria-selected={active === t}
              disabled={!held && t !== "buy"}
              onClick={() => pick(t)}
            >
              {t === "buy" ? "Buy" : t === "sell" ? "Sell" : "Rebalance"}
            </button>
          ))}
        </div>
      </div>

      {active === "buy" && <BuyTab key="buy" {...shared} />}
      {active === "sell" && held && <SellTab key="sell" {...shared} held={held} />}
      {active === "rebalance" && held && <RebalanceTab key="rebalance" {...shared} held={held} />}
    </div>
  );
}

const plural = (n: number, word: string) => (n === 1 ? word : `${word}s`);

function HoldingSummary({ held, onRebalance }: { held: BasketHoldings; onRebalance: () => void }) {
  const ahead = held.holding_return !== null && held.index_return !== null ? held.holding_return - held.index_return : null;
  return (
    <div className="ticket-held">
      <div className="ticket-head">
        <h2>Your position</h2>
      </div>
      <dl className="ticket-rows">
        <Row label="Market value" value={usd(held.value)} tip="Your contracts valued at current mid prices." />
        <Row
          label="Liquidation value"
          value={usd(held.liquidation_value)}
          tip="What you would receive selling everything now: filled at the best bids, after fees."
        />
        <Row label="Cost basis" value={usd(held.cost)} tip="What you paid for the contracts you still hold, including fees." />
        <Row
          label="Unrealized P&L"
          value={signedUsd(held.unrealized_pnl)}
          tone={held.unrealized_pnl}
          tip="Market value minus cost basis: your gain or loss on open contracts."
        />
        {held.realized_pnl !== 0 && (
          <Row
            label="Realized P&L"
            value={signedUsd(held.realized_pnl)}
            tone={held.realized_pnl}
            tip="Gains or losses locked in by sales and market payouts."
          />
        )}
        {held.holding_return !== null && (
          <Row
            label="Total return"
            value={signedPct(held.holding_return, 2)}
            tone={held.holding_return}
            tip="Change in your position's value per share since you opened it. Deposits and withdrawals don't affect it."
          />
        )}
        {held.index_return !== null && (
          <Row
            label="Index return"
            value={signedPct(held.index_return, 2)}
            tone={held.index_return}
            tip="The basket index's change over the same period, before trading costs. Your benchmark."
          />
        )}
        {ahead !== null && (
          <Row
            label="Tracking difference"
            value={signedPct(ahead, 2)}
            tone={ahead}
            tip={`Total return minus index return. ${
              ahead < 0
                ? "Below zero is the cost of trading: spreads, fees and whole-contract rounding."
                : "Above zero comes from rounding and timing."
            }`}
          />
        )}
      </dl>
      {held.max_drift > DRIFT_ALERT && (
        <div className="ticket-alert" role="status">
          <p>
            A holding has drifted {pct(held.max_drift, 1)} from its target weight.
          </p>
          <button type="button" className="text-btn" onClick={onRebalance}>
            Review rebalance →
          </button>
        </div>
      )}
    </div>
  );
}

function Row({ label, value, tone, tip }: { label: string; value: string; tone?: number; tip?: string }) {
  return (
    <div>
      <dt>
        {label}
        {tip && <InfoTip>{tip}</InfoTip>}
      </dt>
      <dd className={tone === undefined ? undefined : tone < 0 ? "down" : "up"}>{value}</dd>
    </div>
  );
}

interface TabProps {
  basket: Basket;
  account: Account;
  question: (marketId: number) => string;
  busy: boolean;
  done: string | null;
  failed: string | null;
  run: (action: () => Promise<{ account: Account; skipped: SkippedLeg[] }>, message: string) => void;
  onPreview: (p: TradePreview | null) => void;
  onUnauthorized: () => void;
  clear: () => void;
}

function BuyTab({ basket, account, question, busy, done, failed, run, onPreview, onUnauthorized, clear }: TabProps) {
  const [input, setInput] = useState(String(Math.min(DEFAULT_AMOUNT, Math.floor(account.cash))));
  const amount = Number(input);
  const valid = input.trim() !== "" && Number.isFinite(amount) && amount > 0;
  const tooMuch = valid && amount > account.cash;
  const { preview, problem, pricing } = usePreview(
    valid && !tooMuch ? () => previewBuy(basket.id, amount) : null,
    `${input}|${account.cash}|${basket.id}`,
    onPreview,
    onUnauthorized,
  );
  const spend = preview ? -preview.cash_delta : 0;

  return (
    <>
      <label htmlFor="amount" className="invest-label">
        Amount <span>{usd(account.cash)} buying power</span>
      </label>
      <div className="invest-field">
        <span aria-hidden="true">$</span>
        <input
          id="amount"
          inputMode="decimal"
          value={input}
          onChange={(e) => {
            clear();
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
              clear();
              setInput(String(a));
            }}
          >
            {usd(a).replace(".00", "")}
          </button>
        ))}
      </div>

      <Outcome
        problem={tooMuch ? `You have ${usd(account.cash)} available.` : failed ?? problem}
        done={done}
        pricing={pricing}
        preview={preview}
        question={question}
      >
        {preview && (
          <>
            <Row
              label="Markets"
              value={`${preview.orders.length} of ${basket.positions.length}`}
              tip="Markets this order buys into. Closed markets, or ones too small to buy a whole contract, are skipped."
            />
            <Costs preview={preview} />
            <Row label="Buying power after" value={usd(preview.cash_after)} />
            <div className="ticket-total">
              <dt>Estimated cost</dt>
              <dd>{usd(spend)}</dd>
            </div>
          </>
        )}
      </Outcome>

      <button
        type="button"
        className="btn ticket-btn"
        disabled={busy || !preview || pricing || !!problem || tooMuch}
        onClick={() => run(() => buyBasket(basket.id, amount), `Bought. ${usd(spend)} invested.`)}
      >
        {busy ? "Placing orders…" : preview ? `Buy for ${usd(spend)}` : "Buy"}
      </button>
    </>
  );
}

function SellTab({ basket, question, busy, done, failed, run, onPreview, onUnauthorized, clear }: TabProps & { held: BasketHoldings }) {
  const [fraction, setFraction] = useState(1);
  const { preview, problem, pricing } = usePreview(
    () => previewSell(basket.id, fraction),
    `${fraction}|${basket.id}`,
    onPreview,
    onUnauthorized,
  );
  const receive = preview?.cash_delta ?? 0;
  const label = fraction === 1 ? "everything" : pct(fraction);

  return (
    <>
      <p className="invest-label">
        <span>
          Quantity
          <InfoTip>Sells this share of every market in your position, so what&rsquo;s left keeps the same weights.</InfoTip>
        </span>
      </p>
      <div className="invest-chips">
        {SELL_FRACTIONS.map((f) => (
          <button
            key={f}
            type="button"
            className="invest-chip"
            aria-pressed={fraction === f}
            onClick={() => {
              clear();
              setFraction(f);
            }}
          >
            {f === 1 ? "All" : pct(f)}
          </button>
        ))}
      </div>

      <Outcome problem={failed ?? problem} done={done} pricing={pricing} preview={preview} question={question}>
        {preview && (
          <>
            <Row label="Markets" value={`${preview.orders.length}`} />
            <Costs preview={preview} />
            <Row label="Buying power after" value={usd(preview.cash_after)} />
            <div className="ticket-total">
              <dt>Estimated proceeds</dt>
              <dd>{usd(receive)}</dd>
            </div>
          </>
        )}
      </Outcome>

      <button
        type="button"
        className="btn ticket-btn"
        disabled={busy || !preview || pricing || !!problem}
        onClick={() =>
          (fraction < 1 || confirm("Sell everything you hold in this basket?")) &&
          run(() => sellBasket(basket.id, fraction), `Sold ${label}. ${usd(receive)} is back in your cash.`)
        }
      >
        {busy ? "Placing orders…" : preview ? `Sell ${label} for ${usd(receive)}` : "Sell"}
      </button>
    </>
  );
}

function RebalanceTab({ basket, held, question, busy, done, failed, run, onPreview, onUnauthorized }: TabProps & { held: BasketHoldings }) {
  // Re-price after each trade: the holding's value is part of the key.
  const { preview, problem, pricing } = usePreview(
    () => previewRebalance(basket.id),
    `${basket.id}|${held.value}`,
    onPreview,
    onUnauthorized,
  );
  const sells = preview?.orders.filter((o) => o.action === "sell").length ?? 0;
  const buys = (preview?.orders.length ?? 0) - sells;
  const atTarget = problem?.includes("already at target");

  return (
    <>
      <p className="invest-fine">
        Trades your holdings back to the basket&rsquo;s target weights, selling first so the proceeds pay for the
        buys. Resolved markets leave the basket. Largest drift now: {pct(held.max_drift, 1)}.
      </p>

      <Outcome
        problem={atTarget ? null : failed ?? problem}
        done={done ?? (atTarget ? "Your holdings are already on target." : null)}
        pricing={pricing}
        preview={preview}
        question={question}
      >
        {preview && (
          <>
            <Row label="Orders" value={`${sells} ${plural(sells, "sell")}, ${buys} ${plural(buys, "buy")}`} />
            <Costs preview={preview} />
            <div className="ticket-total">
              <dt>Net cash impact</dt>
              <dd>{signedUsd(preview.cash_delta)}</dd>
            </div>
          </>
        )}
      </Outcome>

      <button
        type="button"
        className="btn ticket-btn"
        disabled={busy || !preview || pricing || !!problem}
        onClick={() => run(() => rebalanceHoldings(basket.id), "Rebalanced to target weights.")}
      >
        {busy ? "Placing orders…" : "Rebalance"}
      </button>
    </>
  );
}

function Costs({ preview }: { preview: TradePreview }) {
  return (
    <>
      <Row label="Est. fees" value={usd(preview.fees)} tip="Exchange trading fees for this order." />
      {preview.price_impact >= SHOWN_IMPACT && (
        <Row
          label="Est. slippage"
          value={usd(preview.price_impact)}
          tip="Extra cost from filling past the best price when the top of the order book isn't deep enough."
        />
      )}
    </>
  );
}

// The dry run's numbers, or why there are none, plus any legs the order would skip.
function Outcome({
  problem,
  done,
  pricing,
  preview,
  question,
  children,
}: {
  problem: string | null;
  done: string | null;
  pricing: boolean;
  preview: TradePreview | null;
  question: (marketId: number) => string;
  children: React.ReactNode;
}) {
  return (
    <div className="invest-preview" aria-live="polite">
      {problem && <p className="auth-error">{problem}</p>}
      {done && <p className="invest-done">{done}</p>}
      {!problem && !done && pricing && <p className="invest-fine">Pricing against the live order book…</p>}
      {!problem && !done && preview && !pricing && (
        <>
          <dl className="ticket-rows">{children}</dl>
          {preview.skipped.length > 0 && (
            <ul className="invest-skips">
              {preview.skipped.map((s) => (
                <li key={`${s.market_id}-${s.side}-${s.reason}`}>
                  <span>{question(s.market_id)}</span> {s.reason}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
