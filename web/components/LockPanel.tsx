"use client";

import Link from "next/link";
import { useState } from "react";
import { UnauthorizedError } from "@/lib/auth";
import { previewSell, sellBasket, type Account, type BasketHoldings } from "@/lib/accounts";
import { ARBITRAGE_ENABLED, KIND_LABEL } from "@/lib/arbitrage";
import type { Basket } from "@/lib/baskets";
import { date, errorText, usd } from "@/lib/format";

interface Props {
  basket: Basket;
  held: BasketHoldings | null;
  onTraded: (a: Account) => void;
  onUnauthorized: () => void;
}

// Side card for a locked arbitrage: what it cost, what it's guaranteed to pay, and a way to unwind it.
// Takes the place of the trade ticket and automations, which would trade the legs apart.
export function LockPanel({ basket, held, onTraded, onUnauthorized }: Props) {
  const lock = basket.lock!;
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const counts = new Map(held?.holdings.map((h) => [`${h.market.id}-${h.side}`, h.contracts]));
  // A set is one contract of every leg.
  const heldSets = Math.min(...basket.positions.map((p) => counts.get(`${p.market.id}-${p.side}`) ?? 0));

  async function unwind() {
    setBusy(true);
    setProblem(null);
    try {
      const preview = await previewSell(basket.id, 1);
      const ok = confirm(
        `Sell every leg now for about ${usd(preview.cash_delta)}? That gives up the ${usd(
          heldSets * lock.payout_per_set,
        )} these sets pay at settlement.`,
      );
      if (ok) onTraded((await sellBasket(basket.id, 1)).account);
    } catch (err) {
      if (err instanceof UnauthorizedError) return onUnauthorized();
      setProblem(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ticket">
      <div className="ticket-head">
        <h2>Locked arbitrage</h2>
        <span className="ticket-mode">{KIND_LABEL[lock.kind]}</span>
      </div>
      <dl className="ticket-rows">
        <div>
          <dt>Sets held</dt>
          <dd>{heldSets.toLocaleString()}</dd>
        </div>
        <div>
          <dt>Paid, with fees</dt>
          <dd>{usd(lock.cost)}</dd>
        </div>
        <div>
          <dt>Pays at least</dt>
          <dd>{usd(lock.payout)}</dd>
        </div>
        {held && (
          <div>
            <dt>Worth now, at mid prices</dt>
            <dd>{usd(held.value)}</dd>
          </div>
        )}
        <div className="ticket-total">
          <dt>Locked profit</dt>
          <dd className="up">{usd(lock.profit)}</dd>
        </div>
      </dl>
      <p className="invest-fine lock-why">{lock.explanation}</p>
      {lock.closes_at && <p className="invest-fine">Settles by {date(lock.closes_at)}; the payout lands as cash then.</p>}
      {heldSets === 0 && <p className="invest-fine">Nothing is held now: the lock was sold or has paid out (see Activity).</p>}
      {ARBITRAGE_ENABLED && (
        <Link className="btn ticket-btn lock-add" href="/arbitrage">
          Add sets from the scanner
        </Link>
      )}
      {heldSets > 0 && (
        <button type="button" className="text-btn lock-unwind" onClick={unwind} disabled={busy}>
          {busy ? "Pricing…" : "Unwind: sell every leg now"}
        </button>
      )}
      {problem && <p className="auth-error">{problem}</p>}
      <p className="invest-fine">
        Held to settlement. Rebalancing, recurring buys, price triggers and news shifts are off here, since they would
        trade the legs apart and break the floor.
      </p>
    </div>
  );
}
