"use client";

import { InfoTip } from "./InfoTip";
import type { ClimateMode, ClimateSettings, Regime } from "@/lib/climate";

const MODES: [ClimateMode, string][] = [
  ["off", "Off"],
  ["suggest", "Review first"],
  ["auto", "Automatic"],
];
const SENSITIVITY: [number, string][] = [
  [0.25, "Low"],
  [0.5, "Medium"],
  [1, "High"],
];
const MAX_SHIFT = [0.02, 0.05, 0.1];

const MODE_NOTE: Record<ClimateMode, string> = {
  off: "News can still be shown, but it will not change your holdings.",
  suggest: "Review suggested weight changes and choose whether to rebalance.",
  auto: "Rebalance automatically when assessed news calls for a change.",
};

interface Props {
  settings: ClimateSettings;
  regime: Regime | null;
  onChange: (next: ClimateSettings) => void;
  busy?: boolean;
  error?: string | null;
}

// How strongly the basket's weights follow the news climate.
export function ClimateSettingsCard({ settings, regime, onChange, busy, error }: Props) {
  return (
    <div className="ticket auto">
      <div className="ticket-head">
        <h2>News response</h2>
        {regime && (
          <span className="ticket-mode">
            <span className={`clim-dot ${regime}`} aria-hidden="true" /> {regime[0].toUpperCase() + regime.slice(1)}
          </span>
        )}
      </div>

      <div className="ticket-tabs clim-modes" role="tablist" aria-label="News response">
        {MODES.map(([m, label]) => (
          <button
            key={m}
            type="button"
            role="tab"
            className="ticket-tab"
            aria-selected={settings.mode === m}
            disabled={busy}
            onClick={() => onChange({ ...settings, mode: m })}
          >
            {label}
          </button>
        ))}
      </div>
      <p className="invest-fine clim-mode-note">{MODE_NOTE[settings.mode]}</p>
      <p className="invest-fine">Paper trading only. Planned sales finish before other holdings in this basket are bought.</p>
      {error && <p className="auth-error" role="alert">{error}</p>}

      <label className="auto-row">
        <span>
          Sensitivity
          <InfoTip>How much accepted news can change a holding&rsquo;s weight. High makes larger changes; low makes smaller ones.</InfoTip>
        </span>
        <select
          value={settings.sensitivity}
          disabled={busy || settings.mode === "off"}
          onChange={(e) => onChange({ ...settings, sensitivity: Number(e.target.value) })}
        >
          {!SENSITIVITY.some(([v]) => v === settings.sensitivity) && <option value={settings.sensitivity}>{settings.sensitivity} (custom)</option>}
          {SENSITIVITY.map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </select>
      </label>

      <label className="auto-row">
        <span>
          Maximum weight change
          <InfoTip>The most one holding&rsquo;s share can change in a single rebalance. A 5 percentage point limit can move a holding from 20% to 15% or 25%.</InfoTip>
        </span>
        <select
          value={settings.max_shift}
          disabled={busy || settings.mode === "off"}
          onChange={(e) => onChange({ ...settings, max_shift: Number(e.target.value) })}
        >
          {!MAX_SHIFT.includes(settings.max_shift) && <option value={settings.max_shift}>{Number((settings.max_shift * 100).toFixed(2))} points (custom)</option>}
          {MAX_SHIFT.map((v) => (
            <option key={v} value={v}>
              {v * 100} points
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
