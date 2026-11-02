"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AutomationPanel } from "@/components/AutomationPanel";
import { BasketName } from "@/components/BasketName";
import { useBaskets } from "@/components/BasketsContext";
import { NavChart } from "@/components/NavChart";
import { OrderHistory } from "@/components/OrderHistory";
import { PositionTable } from "@/components/PositionTable";
import { TradePanel } from "@/components/TradePanel";
import { UnauthorizedError } from "@/lib/auth";
import {
  getAccount,
  getStakeHistory,
  listOrders,
  type Account,
  type BasketOrder,
  type StakePoint,
  type TradePreview,
} from "@/lib/accounts";
import {
  BASE_NAV,
  deleteBasket,
  getBasket,
  getNavHistory,
  type Basket,
  type NavPoint,
} from "@/lib/baskets";
import { date, dateTime, errorText, pct, signedPct, usd } from "@/lib/format";
import { usePoll } from "@/lib/poll";

// Categories past this many are folded into "Other".
const TOP_CATEGORIES = 5;

const legKey = (marketId: number, side: string) => `${marketId}-${side}`;

export default function Page() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const { refresh, baskets, titles } = useBaskets();
  const [basket, setBasket] = useState<Basket | null>(null);
  // When `basket` was read: the chart ends with its live value at this time.
  const [pricedAt, setPricedAt] = useState<string | null>(null);
  // The sidebar's copy has the latest name: a just-saved basket gets its title after it opens.
  const name =
    (basket && baskets?.find((b) => b.id === basket.id)?.name) ??
    basket?.name ??
    "";
  const [history, setHistory] = useState<NavPoint[]>([]);
  // The chart point under the pointer; the header shows it instead of the latest value.
  const [hover, setHover] = useState<NavPoint | null>(null);
  const [account, setAccount] = useState<Account | null>(null);
  const [orders, setOrders] = useState<BasketOrder[]>([]);
  // The holding's value per share after each trade and sync, drawn over the NAV.
  const [mine, setMine] = useState<StakePoint[]>([]);
  const [preview, setPreview] = useState<TradePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  function fail(err: unknown) {
    if (err instanceof UnauthorizedError) router.replace("/login");
    else setError(errorText(err));
  }

  // Trades, and the holding's history, are extra: without them the page still works.
  function loadActivity() {
    listOrders(Number(id))
      .then(setOrders)
      .catch(() => setOrders([]));
    getStakeHistory(Number(id))
      .then(setMine)
      .catch(() => setMine([]));
  }

  async function loadBasket() {
    const next = await getBasket(id);
    setBasket(next);
    setPricedAt(new Date().toISOString());
  }

  useEffect(() => {
    loadBasket().catch(fail);
    getAccount().then(setAccount).catch(fail);
    // The chart is extra: without history the page still works.
    getNavHistory(id)
      .then(setHistory)
      .catch(() => setHistory([]));
    loadActivity();
  }, [id]);

  // Follow live prices. A failed poll keeps what's shown; the next one tries again.
  usePoll(() => {
    loadBasket().catch(() => {});
    getAccount().then(setAccount).catch(() => {});
  });

  async function remove() {
    if (!basket || !confirm(`Delete "${name}"?`)) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteBasket(basket.id);
      await refresh();
      router.push("/basket");
    } catch (err) {
      setDeleteError(errorText(err));
      setDeleting(false);
    }
  }

  // A trade moves the basket's value too (a rebalance resets its weights), so reload it alongside the account.
  async function traded(next: Account) {
    setAccount(next);
    setPreview(null);
    loadActivity();
    if (basket) await loadBasket();
    refresh();
  }

  const held = account?.baskets.find((b) => b.basket_id === basket?.id) ?? null;

  const previewOrders = new Map(
    preview?.orders.map((o) => [legKey(o.market_id, o.side), o]),
  );
  const skipped = new Map(
    preview?.skipped.map((s) => [legKey(s.market_id, s.side), s]),
  );
  const holdings = new Map(
    held?.holdings.map((h) => [legKey(h.market.id, h.side), h]),
  );
  const questions = new Map<number, string>([
    ...(held?.holdings.map((h) => [h.market.id, h.market.question] as const) ?? []),
    ...(basket?.positions.map((p) => [p.market.id, p.market.question] as const) ?? []),
  ]);
  const question = (marketId: number) => questions.get(marketId) ?? `Market ${marketId}`;

  function note(marketId: number, side: string, market: Basket["positions"][number]["market"]) {
    const key = legKey(marketId, side);
    const parts = [];
    if (market.settlement_price != null) {
      const won = side === "yes" ? market.settlement_price : 1 - market.settlement_price;
      parts.push(
        <span className="pt-skip">
          Resolved {market.settlement_price >= 0.5 ? "Yes" : "No"}: paid {usd(won)} a contract. Leaves the basket at the next rebalance.
        </span>,
      );
    } else if (market.is_open === false) {
      parts.push(<span className="pt-skip">Closed, waiting for the result</span>);
    }
    const order = previewOrders.get(key);
    if (order)
      parts.push(
        `This order ${order.action === "buy" ? "buys" : "sells"} ${order.count} for ${usd(Math.abs(order.cash_delta))}`,
      );
    const skip = skipped.get(key);
    if (skip) parts.push(<span className="pt-skip">Skipped: {skip.reason}</span>);
    const holding = holdings.get(key);
    if (holding)
      parts.push(
        `You hold ${holding.contracts} · ${pct(holding.weight)} of your holding (target ${pct(holding.target_weight)})`,
      );
    return parts.length ? parts.map((p, i) => <span key={i}>{p}</span>) : null;
  }

  if (!basket) {
    return (
      <div className="bd">
        <div className="bd-in">
          {error ? (
            <p className="bd-status auth-error" role="alert">
              {error}
            </p>
          ) : (
            <p className="bd-status">Loading…</p>
          )}
        </div>
      </div>
    );
  }

  const shown = hover?.nav ?? basket.nav;
  const change = shown / BASE_NAV - 1;
  const now = Date.now();
  const nextClose = basket.positions
    .map((p) => p.market.closes_at)
    .filter((c): c is string => !!c && new Date(c).getTime() > now)
    .sort()[0];

  return (
    <div className="bd">
      <div className="bd-in">
        <header className="bd-head">
          <p className="bd-meta">
            Created {date(basket.created_at)}
            {nextClose && <> · First market closes {date(nextClose)}</>}
          </p>
          <h1 className="bd-title">
            <BasketName name={name} state={titles[basket.id]} />
          </h1>
          {basket.description && (
            <p className="bd-desc">{basket.description}</p>
          )}
        </header>

        {error && (
          <p className="bd-status auth-error" role="alert">
            {error}
          </p>
        )}

        <div className="bd-grid">
          <div className="bd-main">
            <section aria-label="Performance">
              <div className="bd-quote">
                <span className="bd-nav">{shown.toFixed(2)}</span>
                <span className={change < 0 ? "down" : "up"}>
                  {signedPct(change, 2)}
                </span>
                <span className="bd-when">
                  {hover ? dateTime(hover.ts) : "since created"}
                </span>
              </div>
              <NavChart
                points={pricedAt ? [...history, { ts: pricedAt, nav: basket.nav }] : history}
                mine={mine.flatMap((p) => (p.share_value === null ? [] : [{ ts: p.ts, nav: p.share_value }]))}
                onHover={setHover}
              />
            </section>

            <section className="bd-section">
              <h2>
                Holdings{" "}
                <span className="bd-count">{basket.positions.length}</span>
              </h2>
              <PositionTable
                rows={basket.positions.map((p) => ({
                  market: p.market,
                  side: p.side,
                  price: p.price,
                  weight: p.target_weight,
                  note: note(p.market.id, p.side, p.market),
                }))}
              />
              <p className="bd-foot">
                <Categories basket={basket} />
              </p>
              <p className="bd-foot">
                A Yes contract pays $1 if the event happens; a No contract pays
                $1 if it doesn&rsquo;t. Price is today&rsquo;s cost per
                contract. Weights add up to 100%.
              </p>
            </section>

            <section className="bd-section">
              <h2>
                Activity <span className="bd-count">{orders.length}</span>
              </h2>
              <OrderHistory orders={orders} question={question} />
            </section>

            <div className="bd-danger">
              <button
                type="button"
                className="text-btn"
                onClick={remove}
                disabled={deleting}
              >
                {deleting ? "Deleting…" : "Delete basket"}
              </button>
              {deleteError && (
                <span className="auth-error" role="alert">
                  {deleteError}
                </span>
              )}
            </div>
          </div>

          <aside className="bd-side">
            {account ? (
              <>
                <AccountLine account={account} />
                <TradePanel
                  basket={basket}
                  account={account}
                  held={held}
                  question={question}
                  onPreview={setPreview}
                  onTraded={traded}
                  onUnauthorized={() => router.replace("/login")}
                />
                <AutomationPanel
                  basketId={basket.id}
                  nav={basket.nav}
                  holding={!!held}
                  onUnauthorized={() => router.replace("/login")}
                />
              </>
            ) : (
              !error && <p className="bd-status">Loading account…</p>
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}

// Share of the basket in each category, largest first, e.g. "Politics 42% · Macro 31%".
function Categories({ basket }: { basket: Basket }) {
  const byCategory = new Map<string, number>();
  for (const p of basket.positions) {
    const c = p.market.category || "Other";
    byCategory.set(c, (byCategory.get(c) ?? 0) + p.target_weight);
  }
  const sorted = [...byCategory].sort((a, b) => b[1] - a[1]);
  const rows = sorted.slice(0, TOP_CATEGORIES);
  const rest = sorted.slice(TOP_CATEGORIES).reduce((s, [, w]) => s + w, 0);
  if (rest > 0) rows.push(["Other", rest]);
  return <>By category: {rows.map(([c, w]) => `${c} ${pct(w)}`).join(" · ")}</>;
}

// The whole paper account at a glance, above the ticket.
function AccountLine({ account }: { account: Account }) {
  const change = account.pnl / account.starting_cash;
  return (
    <p className="bd-account">
      <span>
        Account {usd(account.equity)}{" "}
        <span className={change < 0 ? "down" : "up"}>{signedPct(change, 2)}</span>
      </span>
      <span>Cash {usd(account.cash)}</span>
    </p>
  );
}
