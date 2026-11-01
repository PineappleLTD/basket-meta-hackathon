"use client";

import { useEffect, useState } from "react";
import { UnauthorizedError } from "@/lib/auth";
import {
  cancelConditionalOrder,
  createConditionalOrder,
  getAutomation,
  listConditionalOrders,
  setAutomation,
  type Automation,
  type AutomationIn,
  type ConditionalOrder,
  type ConditionalOrderIn,
} from "@/lib/accounts";
import { dateTime, errorText, pct, usd } from "@/lib/format";

const DRIFT_OPTIONS = [0.03, 0.05, 0.1];
const RECURRING_OPTIONS: [number, string][] = [
  [7, "Every week"],
  [14, "Every 2 weeks"],
  [30, "Every month"],
];
// Past triggers kept on screen, newest first.
const SHOWN_HISTORY = 3;

type TriggerKind = "stop" | "take" | "dip";

const TRIGGERS: Record<TriggerKind, { label: string; below: boolean; offset: number }> = {
  stop: { label: "Stop-loss: sell if the NAV falls to", below: true, offset: -0.1 },
  take: { label: "Take-profit: sell if the NAV rises to", below: false, offset: 0.1 },
  dip: { label: "Buy the dip: buy if the NAV falls to", below: true, offset: -0.05 },
};

interface Props {
  basketId: number;
  nav: number;
  holding: boolean;
  onUnauthorized: () => void;
}

// Trades that place themselves on each price sync: auto-rebalancing, recurring buys, and NAV triggers.
export function AutomationPanel({ basketId, nav, holding, onUnauthorized }: Props) {
  const [automation, setState] = useState<Automation | null>(null);
  const [triggers, setTriggers] = useState<ConditionalOrder[]>([]);
  const [error, setError] = useState<string | null>(null);

  function fail(err: unknown) {
    if (err instanceof UnauthorizedError) onUnauthorized();
    else setError(errorText(err));
  }

  useEffect(() => {
    getAutomation(basketId).then(setState).catch(fail);
    listConditionalOrders(basketId).then(setTriggers).catch(fail);
  }, [basketId]);

  async function save(body: AutomationIn) {
    setError(null);
    try {
      setState(await setAutomation(basketId, body));
    } catch (err) {
      fail(err);
    }
  }

  if (!automation) return null;
  const current: AutomationIn = {
    auto_rebalance_drift: automation.auto_rebalance_drift,
    recurring_amount: automation.recurring_amount,
    recurring_days: automation.recurring_days,
  };
  const open = triggers.filter((t) => t.status === "open");
  const past = triggers.filter((t) => t.status === "filled" || t.status === "failed").slice(0, SHOWN_HISTORY);

  return (
    <div className="ticket auto">
      <div className="ticket-head">
        <h2>Automate</h2>
        <span className="ticket-mode">Checked on each price sync</span>
      </div>

      <label className="auto-row">
        <span>Rebalance automatically</span>
        <select
          value={automation.auto_rebalance_drift ?? ""}
          onChange={(e) => save({ ...current, auto_rebalance_drift: e.target.value ? Number(e.target.value) : null })}
        >
          <option value="">Off</option>
          {DRIFT_OPTIONS.map((d) => (
            <option key={d} value={d}>
              At {pct(d)} drift
            </option>
          ))}
        </select>
      </label>

      <Recurring automation={automation} onSave={(amount, days) => save({ ...current, recurring_amount: amount, recurring_days: days })} />

      {automation.note && <p className="invest-fine">{automation.note}</p>}

      <div className="auto-triggers">
        <h3>Price triggers</h3>
        {open.length === 0 && <p className="invest-fine">None set.</p>}
        <ul className="auto-list">
          {open.map((t) => (
            <li key={t.id}>
              <span>{describe(t)}</span>
              <button
                type="button"
                className="text-btn"
                onClick={async () => {
                  try {
                    await cancelConditionalOrder(t.id);
                    setTriggers((all) => all.map((x) => (x.id === t.id ? { ...x, status: "cancelled" } : x)));
                  } catch (err) {
                    fail(err);
                  }
                }}
              >
                Cancel
              </button>
            </li>
          ))}
          {past.map((t) => (
            <li key={t.id} className="auto-past">
              <span>
                {describe(t)} — {t.status === "filled" ? `filled ${dateTime(t.triggered_at!)}` : `failed: ${t.note}`}
              </span>
            </li>
          ))}
        </ul>
        <NewTrigger
          nav={nav}
          holding={holding}
          onCreate={async (body) => {
            setError(null);
            try {
              const created = await createConditionalOrder(basketId, body);
              setTriggers((all) => [created, ...all]);
              return true;
            } catch (err) {
              fail(err);
              return false;
            }
          }}
        />
      </div>

      {error && (
        <p className="auth-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function describe(t: ConditionalOrder) {
  const at = `NAV ${t.condition === "at_or_below" ? "≤" : "≥"} ${t.nav.toFixed(2)}`;
  if (t.action === "buy") return `Buy ${usd(t.amount)} at ${at}`;
  return `Sell ${t.amount >= 1 ? "all" : pct(t.amount)} at ${at}`;
}

function Recurring({ automation, onSave }: { automation: Automation; onSave: (amount: number | null, days: number | null) => void }) {
  const [amount, setAmount] = useState(String(automation.recurring_amount ?? 100));
  const [days, setDays] = useState(automation.recurring_days ?? 0);
  const value = Number(amount);
  const dirty = days !== (automation.recurring_days ?? 0) || (days > 0 && value !== automation.recurring_amount);
  const valid = days === 0 || (Number.isFinite(value) && value > 0);

  return (
    <div className="auto-recurring">
      <label className="auto-row">
        <span>Recurring buy</span>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
          <option value={0}>Off</option>
          {RECURRING_OPTIONS.map(([d, label]) => (
            <option key={d} value={d}>
              {label}
            </option>
          ))}
        </select>
      </label>
      {days > 0 && (
        <div className="auto-inline">
          <div className="invest-field auto-amount">
            <span aria-hidden="true">$</span>
            <input
              aria-label="Recurring amount"
              inputMode="decimal"
              value={amount}
              onChange={(e) => setAmount(e.target.value.replace(/[^0-9.]/g, ""))}
            />
          </div>
          {dirty && (
            <button type="button" className="btn auto-save" disabled={!valid} onClick={() => onSave(value, days)}>
              Save
            </button>
          )}
        </div>
      )}
      {days === 0 && dirty && (
        <button type="button" className="text-btn" onClick={() => onSave(null, null)}>
          Turn off recurring buy
        </button>
      )}
      {automation.next_recurring_at && !dirty && (
        <p className="invest-fine">
          Next: {usd(automation.recurring_amount!)} on {dateTime(automation.next_recurring_at)}
        </p>
      )}
    </div>
  );
}

function NewTrigger({
  nav,
  holding,
  onCreate,
}: {
  nav: number;
  holding: boolean;
  onCreate: (body: ConditionalOrderIn) => Promise<boolean>;
}) {
  const [kind, setKind] = useState<TriggerKind>(holding ? "stop" : "dip");
  const [level, setLevel] = useState((nav * (1 + TRIGGERS[kind].offset)).toFixed(2));
  const [amount, setAmount] = useState("100");
  const [busy, setBusy] = useState(false);
  const trigger = TRIGGERS[kind];
  const target = Number(level);
  const buy = kind === "dip";

  function choose(next: TriggerKind) {
    setKind(next);
    setLevel((nav * (1 + TRIGGERS[next].offset)).toFixed(2));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    await onCreate({
      action: buy ? "buy" : "sell",
      amount: buy ? Number(amount) : 1,
      condition: trigger.below ? "at_or_below" : "at_or_above",
      nav: target,
    });
    setBusy(false);
  }

  const move = target / nav - 1;
  return (
    <form className="auto-new" onSubmit={submit}>
      <select aria-label="Trigger type" value={kind} onChange={(e) => choose(e.target.value as TriggerKind)}>
        {(Object.keys(TRIGGERS) as TriggerKind[])
          .filter((k) => holding || k === "dip")
          .map((k) => (
            <option key={k} value={k}>
              {TRIGGERS[k].label}
            </option>
          ))}
      </select>
      <div className="auto-inline">
        <div className="invest-field auto-amount">
          <span aria-hidden="true">NAV</span>
          <input aria-label="NAV level" inputMode="decimal" value={level} onChange={(e) => setLevel(e.target.value.replace(/[^0-9.]/g, ""))} />
        </div>
        {buy && (
          <div className="invest-field auto-amount">
            <span aria-hidden="true">$</span>
            <input aria-label="Amount to buy" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^0-9.]/g, ""))} />
          </div>
        )}
        <button type="submit" className="btn auto-save" disabled={busy || !(target > 0) || (buy && !(Number(amount) > 0))}>
          Add
        </button>
      </div>
      <p className="invest-fine">
        Now {nav.toFixed(2)}
        {Number.isFinite(move) && target > 0 && ` · ${move >= 0 ? "+" : ""}${pct(move, 1)} from here`}. Fires once, at
        market{buy ? "" : ", selling everything in this basket"}.
      </p>
    </form>
  );
}
