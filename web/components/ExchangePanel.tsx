"use client";

import { useEffect, useState } from "react";
import { exchangeStatus, exchangeState, connectExchange, startExchange, armExchange, cancelExchange,
  reviewExchange, reconcileExchange, type ExchangeStatus, type ExchangeState } from "@/lib/exchange";
import { errorText, usd } from "@/lib/format";
import { usePoll } from "@/lib/poll";

export function ExchangePanel({ basketId }: { basketId: number }) {
  const [status, setStatus] = useState<ExchangeStatus | null>(null);
  const [state, setState] = useState<ExchangeState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function load() {
    try {
      setError(null);
      const current = await exchangeStatus();
      setStatus(current);
      if (current.enabled_for_user) {
        try { setState(await exchangeState(basketId)); }
        catch (err) { setState(null); setError(errorText(err)); }
      }
    } catch (err) { setError(errorText(err)); }
  }
  useEffect(() => { setState(null); load(); }, [basketId]);
  usePoll(() => { if (status?.enabled_for_user && !busy) load(); });
  async function act(action: () => Promise<unknown>, consequential = false) {
    if (consequential && status?.environment === "production" && !window.confirm("This can send real exchange orders for this ETF. Continue?")) return;
    setBusy(true); setError(null);
    try { await action(); await load(); }
    catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  }
  const pending = !!state?.cycle && ["SELLING", "BUYING"].includes(state.cycle.status);
  const needsReview = !!state?.cycle && ["EXPIRED", "CANCELLED", "BLOCKED"].includes(state.cycle.status);
  return <details className="ticket auto fold" aria-label="Exchange execution" open={needsReview || undefined}>
    <summary className="ticket-head"><h2>Exchange execution</h2><span className="ticket-mode">{status?.enabled_for_user ? `${status.environment}${state?.armed ? " · armed" : ""}` : "Off"}</span></summary>
    {!status?.enabled_for_user ? <p className="invest-fine">Exchange orders are disabled. Use paper trading to test the news response.</p> : !state ? <>
      <p className="invest-fine">Connect the configured subaccount to this ETF. Only its existing markets and held sides can be imported. Connection alone does not enable automation.</p>
      <button type="button" className="btn auto-save" disabled={busy} onClick={() => act(() => connectExchange(basketId))}>Connect ETF</button>
    </> : <>
      <p className="invest-fine">{status.environment === "production" ? "Real exchange account" : "Demo exchange account"} · Automation {state.armed ? "on" : "off"} · Reserved cash {usd(state.basket_cash)}</p>
      {state.cycle && <div role="status"><p className="invest-fine">{state.cycle.status.toLowerCase()}: {state.cycle.note}</p></div>}
      {state.cycle && <p className="invest-fine">Original sales: {state.cycle.sells.reduce((n, leg) => n + leg.filled, 0)}/{state.cycle.sells.reduce((n, leg) => n + leg.count, 0)} confirmed.</p>}
      {state.orders.length > 0 && <ul className="clim-feed">{state.orders.map(o => <li key={o.client_id}>
        <p className="clim-meta">{o.phase.toLowerCase()} · {o.confirmed}/{o.requested} confirmed · {o.status.toLowerCase()}</p>
        {o.note && <p className="invest-fine">{o.note}</p>}
      </li>)}</ul>}
      {pending ? <>
        <button type="button" className="text-btn" disabled={busy} onClick={() => act(() => reconcileExchange(basketId))}>Check confirmed fills</button>{" "}
        <button type="button" className="text-btn" disabled={busy} onClick={() => act(() => cancelExchange(basketId))}>Cancel remaining orders</button>
      </> : needsReview ? <button type="button" className="btn auto-save" disabled={busy} onClick={() => act(() => reviewExchange(basketId))}>Keep confirmed holdings and cash</button> : <>
        {state.proposal.reason ? <p className="invest-fine">{state.proposal.reason}</p> : <button type="button" className="btn auto-save" disabled={busy} onClick={() => act(() => startExchange(basketId, state.proposal.id), true)}>Start {status.environment} rebalance</button>}
        <button type="button" className="text-btn" disabled={busy} onClick={() => act(() => armExchange(basketId, !state.armed), !state.armed)}>{state.armed ? "Disable" : "Enable"} exchange automation</button>
      </>}
    </>}
    {error && <p className="auth-error" role="alert">{error}</p>}
  </details>;
}
