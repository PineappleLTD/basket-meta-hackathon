"use client";

import { useMemo, useState } from "react";
import { InfoTip } from "./InfoTip";
import type { Position } from "@/lib/baskets";
import {
  applyShifts,
  proposeShifts,
  turnover,
  type Climate,
  type ClimateSettings,
  type ClimateSignal,
  type Regime,
  type Shift,
} from "@/lib/climate";
import { dateTime, errorText, pct, signedPct } from "@/lib/format";

const REGIMES: [Regime, string][] = [
  ["calm", "Calm"],
  ["shifting", "Shifting"],
  ["turbulent", "Turbulent"],
];
// Signals kept on screen before "Show all".
const SHOWN_SIGNALS = 5;

interface Props {
  basketId: number;
  positions: Position[];
  climate: Climate | null;
  settings: ClimateSettings;
  question: (marketId: number) => string;
}

// What the news classifier is reading around this basket, and the weights it would move toward.
export function ClimatePanel({ basketId, positions, climate, settings, question }: Props) {
  const shifts = useMemo(
    () => (climate ? proposeShifts(positions, climate.signals, settings) : []),
    [positions, climate, settings],
  );

  return (
    <section className="bd-section clim">
      <h2>
        News climate <span className="tag">Preview</span>
        <InfoTip>
          A classifier reads the news that touches this basket&rsquo;s markets, scores each story for direction and
          confidence, and suggests target weights that lean toward the side the news favours.
        </InfoTip>
      </h2>

      {!climate ? (
        <p className="bd-foot">Reading the news…</p>
      ) : (
        <>
          <RegimeStrip climate={climate} />
          {climate.topics.length > 0 && <Topics climate={climate} />}
          <Proposal basketId={basketId} shifts={shifts} settings={settings} signals={climate.signals} question={question} />
          <Signals signals={climate.signals} question={question} />
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
}: {
  basketId: number;
  shifts: Shift[];
  settings: ClimateSettings;
  signals: ClimateSignal[];
  question: (marketId: number) => string;
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
      const res = await applyShifts(basketId, moved);
      setApplied(res.applied_at);
    } catch (err) {
      setError(errorText(err));
    }
    setBusy(false);
  }

  return (
    <div className="clim-block">
      <h3>
        Proposed reweight
        {settings.mode !== "off" && moved.length > 0 && <span className="bd-count">{pct(traded, 1)} turnover</span>}
      </h3>

      {settings.mode === "off" ? (
        <p className="bd-foot">Climate reweighting is off. Turn it on in the sidebar to see suggested weights.</p>
      ) : moved.length === 0 || dismissed ? (
        <p className="bd-foot">
          {dismissed ? "Dismissed. A new proposal appears when the news moves again." : "The news doesn’t move any weight past the threshold. Nothing to change."}
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
            <p className="bd-foot">Auto mode: these weights apply at the next price sync, then your holding rebalances to them.</p>
          ) : applied ? (
            <p className="bd-foot">
              <span className="invest-done">Applied {dateTime(applied)}.</span> Your holding moves to these weights at the next rebalance.
            </p>
          ) : (
            <div className="clim-actions">
              <button type="button" className="btn auto-save" onClick={apply} disabled={busy}>
                {busy ? "Applying…" : "Apply new weights"}
              </button>
              <button type="button" className="text-btn" onClick={() => setDismissed(true)} disabled={busy}>
                Dismiss
              </button>
              <span className="invest-fine">
                Capped at {pct(settings.max_shift)} a leg · {moved.length} of {shifts.length} legs change
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
        Signals <span className="bd-count">{signals.length}</span>
      </h3>
      {signals.length === 0 ? (
        <p className="bd-foot">No stories in the last day touch this basket.</p>
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
                <span className={s.sentiment >= 0 ? "up" : "down"}>
                  {s.sentiment >= 0 ? "▲ Leans Yes" : "▼ Leans No"} {Math.abs(s.sentiment).toFixed(2)}
                </span>
                <span>{pct(s.confidence)} confidence</span>
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
