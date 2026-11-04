"use client";

import { useState } from "react";
import { InfoTip } from "./InfoTip";
import type { Position } from "@/lib/baskets";
import {
  applyShifts,
  reviewStoppedCycle,
  turnover,
  type Climate,
  type ClimateSettings,
  type ClimateSignal,
  type Regime,
  type Shift,
} from "@/lib/climate";
import { dateTime, errorText, pct, signedPct, usd } from "@/lib/format";

const REGIMES: [Regime, string][] = [
  ["calm", "Calm"],
  ["shifting", "Shifting"],
  ["turbulent", "Turbulent"],
];
// Signals kept on screen before "Show all".
const SHOWN_SIGNALS = 5;
const RESPONSE_NOTES: Record<ClimateSettings["mode"], string> = {
  off: "Off: news will not change this basket's holdings.",
  suggest: "Suggestions: review proposed changes before applying them.",
  auto: "Automatic: the app can rebalance paper holdings when news warrants a change.",
};
const MONITOR_NOTES: Record<string, string> = {
  STARTING: "Starting news checks…",
  CHECKING: "Checking for relevant news…",
  MONITORING: "Watching for news.",
  DEGRADED: "Some news sources are unavailable. Checks continue with the available sources.",
  WAITING_FOR_BASKETS: "Waiting for a saved basket with open markets and complete contract rules.",
  WAITING_FOR_KEY: "Setup needed: add the news service API key to start checks.",
  BUDGET_EXHAUSTED: "Paused: the news request allowance is used up. The app owner can increase it to resume.",
  PAUSED_ERROR: "News checks are paused. See the details below for what needs attention.",
  DISABLED: "News checks are turned off.",
  STANDBY: "Waiting for the active news monitor.",
  STOPPED: "News checks have stopped.",
  UNAVAILABLE: "The news service is not responding. Check that the app's server is running.",
};

interface Props {
  basketId: number;
  positions: Position[];
  climate: Climate | null;
  settings: ClimateSettings;
  question: (marketId: number) => string;
  error?: string | null;
  onApplied: () => void;
}

// What the news classifier is reading around this basket, and the weights it would move toward.
export function ClimatePanel({ basketId, climate, settings, question, error, onApplied }: Props) {
  const shifts = climate?.proposal.shifts ?? [];
  const proposalReason = climate?.proposal.reason === "No new accepted adverse evidence" && climate.signals.length === 0
    ? "No recent news has been assessed for this basket yet."
    : climate?.proposal.reason ?? null;
  const [reviewBusy, setReviewBusy] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  async function review() {
    if (!climate?.cycle) return;
    setReviewBusy(true);
    setReviewError(null);
    try { await reviewStoppedCycle(basketId, climate.cycle.id); onApplied(); }
    catch (err) { setReviewError(errorText(err)); }
    finally { setReviewBusy(false); }
  }

  return (
    <section className="bd-section clim">
      <h2>
        News and rebalancing <span className="tag">Paper only · Experimental</span>
        <InfoTip>
          News is checked against the rules for each market in this basket. A rebalance can reduce an affected holding
          and move the proceeds to other existing holdings after its planned sales finish.
        </InfoTip>
      </h2>

      {!climate ? (
        <p className={error ? "auth-error" : "bd-foot"} role={error ? "alert" : undefined}>{error ?? "Loading saved assessments…"}</p>
      ) : (
        <>
          {error && <p className="auth-error" role="alert">
            News status may be out of date. Showing the previous update from {dateTime(climate.as_of)}.
          </p>}
          <div className="clim-block" role="status" aria-live="polite">
            <h3>News monitoring</h3>
            <p className="bd-foot">{error ? "Status is out of date." : MONITOR_NOTES[climate.monitoring?.status ?? ""] ?? "Monitoring status is unavailable."}</p>
            {climate.monitoring?.checked_at && <p className="bd-foot">Last checked: {dateTime(climate.monitoring.checked_at)}</p>}
            <p className="bd-foot">Recent assessments for this basket: {climate.signals.length}.</p>
            <p className="bd-foot">{RESPONSE_NOTES[settings.mode]}</p>
            {(climate.monitoring || error) && <details className="bd-foot">
              <summary>Monitoring details</summary>
              {error && <p>Refresh error: {error}</p>}
              {climate.monitoring && <>
                <p>{climate.monitoring.note}</p>
                <p>Request allowance used: discovery {climate.monitoring.requests.discovery.used}/{climate.monitoring.requests.discovery.limit}; evidence {climate.monitoring.requests.evidence.used}/{climate.monitoring.requests.evidence.limit}. Shared across baskets and app restarts; retries count.</p>
              </>}
            </details>}
          </div>
          {climate.signals.length > 0 && <RegimeStrip climate={climate} />}
          {climate.missing_rules.length > 0 && <p className="bd-foot">{climate.missing_rules.length} positions need exact exchange rules before they can be scored. Sync market data to load them.</p>}
          <p className="bd-foot">Reserved basket cash: {usd(climate.basket_cash)}. New markets and unheld outcome sides are excluded.</p>
          {climate.cycle && <div className="clim-block" role="status">
            <h3>Paper rebalance: {climate.cycle.status.toLowerCase()}</h3>
            <p className="bd-foot">{climate.cycle.note}</p>
            <p className="bd-foot">Sales confirmed: {climate.cycle.sells.reduce((s, l) => s + l.filled, 0)} of {climate.cycle.sells.reduce((s, l) => s + l.count, 0)} contracts. Purchases wait for every planned sale.</p>
            {["BLOCKED", "EXPIRED", "CANCELLED"].includes(climate.cycle.status) && <>
              <p className="bd-foot">Automatic trades are paused. Keeping the current allocation saves the holdings and cash as they stand, and leaves news response off.</p>
              <button type="button" className="btn auto-save" disabled={reviewBusy} onClick={review}>{reviewBusy ? "Saving…" : "Keep current holdings and ETF cash"}</button>
              {reviewError && <p className="auth-error" role="alert">{reviewError}</p>}
            </>}
          </div>}
          {climate.topics.length > 0 && <Topics climate={climate} />}
          <Proposal key={climate.proposal.id} basketId={basketId} proposalId={climate.proposal.id}
            reason={proposalReason} pending={!!climate.cycle && ["SELLING", "BUYING", "BLOCKED", "EXPIRED", "CANCELLED"].includes(climate.cycle.status)}
            shifts={shifts} settings={settings} signals={climate.signals} question={question} onApplied={onApplied} />
          {climate.signals.length > 0 && <Signals signals={climate.signals} question={question} />}
        </>
      )}
    </section>
  );
}

function RegimeStrip({ climate }: { climate: Climate }) {
  return (
    <div className="clim-regime">
      <ol className="clim-scale" aria-label={`Regime: ${climate.regime}`}>
        {REGIMES.map(([r, label]) => (
          <li key={r} aria-current={r === climate.regime ? "true" : undefined}>
            {label}
          </li>
        ))}
      </ol>
      <p className="clim-summary">{climate.summary}</p>
      <p className="clim-asof">
        Intensity {pct(climate.intensity)} · Read {dateTime(climate.as_of)}
      </p>
    </div>
  );
}

// One diverging bar per topic: right of centre leans Yes, left leans No.
function Topics({ climate }: { climate: Climate }) {
  return (
    <div className="clim-block">
      <h3>
        Pressure by topic
        <InfoTip>Confidence-weighted direction of the stories in each topic. Right of centre favours Yes; left favours No.</InfoTip>
      </h3>
      <ul className="clim-topics">
        {climate.topics.map((t) => (
          <li key={t.topic}>
            <span className="clim-topic">
              {t.topic} <span className="bd-count">{t.count}</span>
            </span>
            <span className="clim-bar" aria-hidden="true">
              <i
                className={t.score >= 0 ? "pos" : "neg"}
                style={t.score >= 0 ? { left: "50%", width: `${t.score * 50}%` } : { right: "50%", width: `${-t.score * 50}%` }}
              />
            </span>
            <span className={`clim-score ${t.score >= 0 ? "up" : "down"}`}>
              {t.score >= 0 ? "Yes" : "No"} {Math.abs(t.score).toFixed(2)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Proposal({
  basketId,
  shifts,
  settings,
  signals,
  question,
  proposalId, reason, pending, onApplied,
}: {
  basketId: number;
  shifts: Shift[];
  settings: ClimateSettings;
  signals: ClimateSignal[];
  question: (marketId: number) => string;
  proposalId: string;
  reason: string | null;
  pending: boolean;
  onApplied: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [applied, setApplied] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const moved = shifts
    .filter((s) => s.proposed_weight !== s.current_weight)
    .sort((a, b) => Math.abs(b.proposed_weight - b.current_weight) - Math.abs(a.proposed_weight - a.current_weight));
  const traded = turnover(shifts);
  const byId = new Map(signals.map((s) => [s.id, s]));

  async function apply() {
    setBusy(true);
    setError(null);
    try {
      const res = await applyShifts(basketId, proposalId);
      setApplied(res.updated_at);
      onApplied();
    } catch (err) {
      setError(errorText(err));
    }
    setBusy(false);
  }

  return (
    <div className="clim-block">
      <h3>
        Suggested changes
        {settings.mode !== "off" && moved.length > 0 && <span className="bd-count">{pct(traded, 1)} turnover</span>}
      </h3>

      {settings.mode === "off" ? (
        <p className="bd-foot">News response is off. Choose Review first or Automatic in the sidebar to enable it.</p>
      ) : pending ? (
        <p className="bd-foot">The saved rebalance must finish or be reviewed before another starts. Purchases wait for all planned sales.</p>
      ) : moved.length === 0 || dismissed || reason ? (
        <p className="bd-foot">
          {dismissed ? "Dismissed. New suggestions appear when the news changes." : reason === "No new accepted adverse evidence" ? "No new assessments call for a weight change." : reason ?? "No assessed news calls for a reduction."}
        </p>
      ) : (
        <>
          <ul className="clim-shifts">
            {moved.map((s) => {
              const delta = s.proposed_weight - s.current_weight;
              return (
                <li key={`${s.market_id}-${s.side}`}>
                  <details>
                    <summary className="clim-shift">
                      <span className="clim-q">
                        <span className={`side ${s.side}`}>{s.side === "yes" ? "Yes" : "No"}</span> {question(s.market_id)}
                      </span>
                      <span className="clim-w">
                        {pct(s.current_weight, 1)} → <b>{pct(s.proposed_weight, 1)}</b>
                      </span>
                      <span className={`clim-d ${delta >= 0 ? "up" : "down"}`}>{signedPct(delta, 1)}</span>
                      <WeightBar from={s.current_weight} to={s.proposed_weight} />
                    </summary>
                    <ul className="clim-why">
                      {s.signal_ids.map((id) => {
                        const sig = byId.get(id);
                        return (
                          sig && (
                            <li key={id}>
                              <span>{sig.headline}</span>
                              <span className="clim-src">
                                {sig.source} · {pct(sig.confidence)} conf.
                              </span>
                            </li>
                          )
                        );
                      })}
                    </ul>
                  </details>
                </li>
              );
            })}
          </ul>

          {settings.mode === "auto" ? (
            <p className="bd-foot">Automatic mode starts this paper rebalance at the next price update. Planned sales finish before other holdings are bought.</p>
          ) : applied ? (
            <p className="bd-foot">
              <span className="invest-done">Started {dateTime(applied)}.</span> Follow the confirmed sale counts above.
            </p>
          ) : (
            <div className="clim-actions">
              <button type="button" className="btn auto-save" onClick={apply} disabled={busy}>
                {busy ? "Starting…" : "Rebalance paper holdings"}
              </button>
              <button type="button" className="text-btn" onClick={() => setDismissed(true)} disabled={busy}>
                Dismiss
              </button>
              <span className="invest-fine">
                Maximum change: {Number((settings.max_shift * 100).toFixed(2))} percentage points per holding · {moved.length} of {shifts.length} holdings change
              </span>
            </div>
          )}
          {error && (
            <p className="auth-error" role="alert">
              {error}
            </p>
          )}
        </>
      )}
    </div>
  );
}

// Current weight as a faint track, the proposed weight drawn over it; scaled so the largest leg fills the bar.
function WeightBar({ from, to }: { from: number; to: number }) {
  const scale = Math.max(from, to, 0.0001) * 1.25;
  return (
    <span className="clim-wbar" aria-hidden="true">
      <i className="was" style={{ width: `${(from / scale) * 100}%` }} />
      <i className={to >= from ? "now up-bg" : "now"} style={{ width: `${(to / scale) * 100}%` }} />
    </span>
  );
}

function Signals({ signals, question }: { signals: ClimateSignal[]; question: (marketId: number) => string }) {
  const [all, setAll] = useState(false);
  const shown = all ? signals : signals.slice(0, SHOWN_SIGNALS);

  return (
    <div className="clim-block">
      <h3>
        Assessments for this basket <span className="bd-count">{signals.length}</span>
      </h3>
      {signals.length === 0 ? (
        <p className="bd-foot">No recent, validated news assessments for this basket.</p>
      ) : (
        <ul className="clim-feed">
          {shown.map((s) => (
            <li key={s.id}>
              <p className="clim-meta">
                {dateTime(s.published_at)} · {s.source}
                <span className="tag">{s.topic}</span>
              </p>
              <p className="clim-head">{s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.headline} ↗</a> : s.headline}</p>
              <p className="clim-meta">
                <span className={s.sentiment > 0 ? "up" : s.sentiment < 0 ? "down" : ""}>{s.classification.toLowerCase()} for the held side</span>
                <span>{pct(s.confidence)} evidence confidence (uncalibrated)</span>
                <span>
                  Touches {s.market_ids.length === 1 ? `“${question(s.market_ids[0])}”` : `${s.market_ids.length} markets`}
                </span>
              </p>
            </li>
          ))}
        </ul>
      )}
      {signals.length > SHOWN_SIGNALS && (
        <button type="button" className="text-btn clim-more" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show all ${signals.length}`}
        </button>
      )}
    </div>
  );
}
