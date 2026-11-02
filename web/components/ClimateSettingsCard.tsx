"use client";

import { InfoTip } from "./InfoTip";
import type { ClimateMode, ClimateSettings, Regime } from "@/lib/climate";
import { pct } from "@/lib/format";

const MODES: [ClimateMode, string][] = [
  ["off", "Off"],
  ["suggest", "Suggest"],
  ["auto", "Auto"],
];
const SENSITIVITY: [number, string][] = [
  [0.25, "Low"],
  [0.5, "Medium"],
  [1, "High"],
];
const MAX_SHIFT = [0.02, 0.05, 0.1];

const MODE_NOTE: Record<ClimateMode, string> = {
  off: "News is still read and shown, but weights never move.",
  suggest: "New weights are proposed under Holdings; nothing trades until you apply them.",
  auto: "New weights apply on each price sync, then your holding rebalances to them.",
};

interface Props {
  settings: ClimateSettings;
  regime: Regime | null;
  onChange: (next: ClimateSettings) => void;
}

// How strongly the basket's weights follow the news climate.
export function ClimateSettingsCard({ settings, regime, onChange }: Props) {
  return (
    <div className="ticket auto">
      <div className="ticket-head">
        <h2>Climate response</h2>
        {regime && (
          <span className="ticket-mode">
            <span className={`clim-dot ${regime}`} aria-hidden="true" /> {regime[0].toUpperCase() + regime.slice(1)}
          </span>
        )}
      </div>

      <div className="ticket-tabs clim-modes" role="tablist" aria-label="Climate reweighting">
        {MODES.map(([m, label]) => (
          <button
            key={m}
            type="button"
            role="tab"
            className="ticket-tab"
            aria-selected={settings.mode === m}
            onClick={() => onChange({ ...settings, mode: m })}
          >
            {label}
          </button>
        ))}
      </div>
      <p className="invest-fine clim-mode-note">{MODE_NOTE[settings.mode]}</p>

      <label className="auto-row">
        <span>
          Sensitivity
          <InfoTip>How far a leg leans per unit of news signal. High follows the news closely; low only moves on strong, confident stories.</InfoTip>
        </span>
        <select
          value={settings.sensitivity}
          disabled={settings.mode === "off"}
          onChange={(e) => onChange({ ...settings, sensitivity: Number(e.target.value) })}
        >
          {SENSITIVITY.map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </select>
      </label>

      <label className="auto-row">
        <span>
          Max move per leg
          <InfoTip>The most any one market&rsquo;s weight can change in a single reweight, as a share of the basket.</InfoTip>
        </span>
        <select
          value={settings.max_shift}
          disabled={settings.mode === "off"}
          onChange={(e) => onChange({ ...settings, max_shift: Number(e.target.value) })}
        >
          {MAX_SHIFT.map((v) => (
            <option key={v} value={v}>
              ±{pct(v)}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
