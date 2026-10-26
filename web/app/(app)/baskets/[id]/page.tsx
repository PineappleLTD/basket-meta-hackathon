"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useBaskets } from "@/components/BasketsContext";
import { UnauthorizedError } from "@/lib/auth";
import { BASE_NAV, deleteBasket, getBasket, type Basket } from "@/lib/baskets";
import { cents, date, errorText, pct, signedPct } from "@/lib/format";

export default function Page() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const { refresh } = useBaskets();
  const [basket, setBasket] = useState<Basket | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    getBasket(id)
      .then(setBasket)
      .catch((err) => (err instanceof UnauthorizedError ? router.replace("/login") : setError(errorText(err))));
  }, [id, router]);

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

  const change = basket ? basket.nav / BASE_NAV - 1 : 0;

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
              NAV {basket.nav.toFixed(2)} <span className={change < 0 ? "down" : "up"}>{signedPct(change)}</span>
            </span>
            <span>{basket.positions.length} positions</span>
            <span>created {date(basket.created_at)}</span>
          </div>

          <ol className="pick-list">
            {basket.positions.map((p) => (
              <li key={`${p.market.id}-${p.side}`}>
                <div className="pick-head">
                  <span className="pick-q">
                    {p.market.url ? (
                      <a href={p.market.url} target="_blank" rel="noreferrer">
                        {p.market.question}
                      </a>
                    ) : (
                      p.market.question
                    )}
                  </span>
                  <span className="mono pick-w">{pct(p.current_weight)}</span>
                </div>
                <div className="pick-meta mono">
                  <span className={`side ${p.side}`}>{p.side}</span>
                  <span>{cents(p.price)}</span>
                  <span>target {pct(p.target_weight)}</span>
                  <span>{p.market.external_id}</span>
                </div>
                <div className="fl-track">
                  <i style={{ width: pct(p.current_weight) }} />
                </div>
              </li>
            ))}
          </ol>

          <button type="button" className="text-btn" onClick={remove} disabled={deleting}>
            {deleting ? "deleting…" : "delete basket"}
          </button>
        </>
      )}
    </div>
  );
}
