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
  suggest: "Review a proposal, then start a paper rebalance. Purchases wait until all planned sales fill.",
  auto: "Rebalance paper holdings on new evidence. Only existing ETF positions can receive proceeds; all planned sales must fill first.",
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
    <details className="ticket auto fold">
      <summary className="ticket-head">
        <h2>Climate response</h2>
        <span className="ticket-mode">
          {regime && <span className={`clim-dot ${regime}`} aria-hidden="true" />} {MODES.find(([m]) => m === settings.mode)?.[1]}
        </span>
      </summary>

      <div className="ticket-tabs clim-modes" role="tablist" aria-label="Climate reweighting">
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
      <p className="invest-fine clim-mode-note">
        {MODE_NOTE[settings.mode]}
        <InfoTip end>
          Paper trading only. The ETF remembers the completed allocation, including cash. Other trades wait while a news
          rebalance is pending.
        </InfoTip>
      </p>
      {error && <p className="auth-error" role="alert">{error}</p>}

      <label className="auto-row">
        <span>
          Sensitivity
          <InfoTip>How far a leg leans per unit of news signal. High follows the news closely; low only moves on strong, confident stories.</InfoTip>
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
          Max move per leg
          <InfoTip>The most any one market&rsquo;s weight can change in a single reweight, as a share of the basket.</InfoTip>
        </span>
        <select
          value={settings.max_shift}
          disabled={busy || settings.mode === "off"}
          onChange={(e) => onChange({ ...settings, max_shift: Number(e.target.value) })}
        >
          {!MAX_SHIFT.includes(settings.max_shift) && <option value={settings.max_shift}>±{pct(settings.max_shift)} (custom)</option>}
          {MAX_SHIFT.map((v) => (
            <option key={v} value={v}>
              ±{pct(v)}
            </option>
          ))}
        </select>
      </label>
    </details>
  );
}
