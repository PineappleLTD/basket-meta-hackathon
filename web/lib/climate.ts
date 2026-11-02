import type { Position } from "./baskets";
import type { Side } from "./theses";

// The news climate around a basket: what the classifier read, and how the weights would move in response.
// Nothing here talks to the API yet. getClimate builds sample signals from the basket's own markets so the UI
// can be built and demoed; swap it for `apiJson<Climate>(`/baskets/${id}/climate`)` once the classifier ships.

export type Regime = "calm" | "shifting" | "turbulent";

export interface ClimateSignal {
  id: string;
  headline: string;
  source: string;
  url: string | null;
  published_at: string;
  // Classifier label, e.g. "Monetary policy".
  topic: string;
  // -1..1: how strongly the story points toward Yes (positive) or No (negative) on the markets it touches.
  sentiment: number;
  // 0..1: the classifier's confidence in the label and sentiment.
  confidence: number;
  market_ids: number[];
}

export interface TopicPressure {
  topic: string;
  // -1..1, the confidence-weighted mean sentiment of its signals.
  score: number;
  count: number;
}

export interface Climate {
  regime: Regime;
  // 0..1: how much the news has moved since the last read.
  intensity: number;
  summary: string;
  as_of: string;
  topics: TopicPressure[];
  // Newest first.
  signals: ClimateSignal[];
}

export type ClimateMode = "off" | "suggest" | "auto";

export interface ClimateSettings {
  mode: ClimateMode;
  // Weight moved per unit of net signal: low 0.25, medium 0.5, high 1.
  sensitivity: number;
  // Largest move any one leg may make, as a share of the basket.
  max_shift: number;
}

export const DEFAULT_CLIMATE_SETTINGS: ClimateSettings = { mode: "suggest", sensitivity: 0.5, max_shift: 0.05 };

export interface Shift {
  market_id: number;
  side: Side;
  current_weight: number;
  proposed_weight: number;
  // Net signal for the side held, -1..1.
  pressure: number;
  signal_ids: string[];
}

// Changes smaller than this are left alone, so the proposal doesn't churn on noise.
const MIN_SHIFT = 0.0025;

/** New target weights: each leg leans toward its net signal, capped at max_shift, then renormalised to 100%. */
export function proposeShifts(positions: Position[], signals: ClimateSignal[], settings: ClimateSettings): Shift[] {
  const legs = positions.map((p) => {
    const touching = signals.filter((s) => s.market_ids.includes(p.market.id));
    const yes = touching.reduce((sum, s) => sum + s.sentiment * s.confidence, 0) / Math.max(touching.length, 1);
    const pressure = p.side === "yes" ? yes : -yes;
    const move = Math.max(-settings.max_shift, Math.min(settings.max_shift, p.target_weight * pressure * settings.sensitivity));
    return { p, pressure, raw: Math.max(0, p.target_weight + move), signal_ids: touching.map((s) => s.id) };
  });
  const total = legs.reduce((s, l) => s + l.raw, 0) || 1;
  return legs.map(({ p, pressure, raw, signal_ids }) => {
    const proposed = raw / total;
    return {
      market_id: p.market.id,
      side: p.side,
      current_weight: p.target_weight,
      proposed_weight: Math.abs(proposed - p.target_weight) < MIN_SHIFT ? p.target_weight : proposed,
      pressure,
      signal_ids,
    };
  });
}

/** Share of the basket that would trade: half the sum of absolute weight changes. */
export const turnover = (shifts: Shift[]) =>
  shifts.reduce((s, x) => s + Math.abs(x.proposed_weight - x.current_weight), 0) / 2;

// Placeholders until the API has these endpoints.
export async function saveClimateSettings(_basketId: number, settings: ClimateSettings) {
  return settings;
}

export async function applyShifts(_basketId: number, shifts: Shift[]) {
  await new Promise((r) => setTimeout(r, 600));
  return { applied_at: new Date().toISOString(), legs: shifts.length };
}

const SOURCES = ["Reuters", "Bloomberg", "AP", "Financial Times", "Politico", "The Information"];
const LEANS_YES = ["New reporting lifts odds on", "Officials signal movement toward", "Fresh data backs", "Insiders now expect"];
const LEANS_NO = ["Doubts grow over", "Setback reported for", "Analysts cut expectations for", "Fresh data undercuts"];

// Stable pseudo-random 0..1 from an integer, so a basket's sample climate doesn't reshuffle on every poll.
function noise(n: number) {
  const x = Math.sin(n * 12.9898) * 43758.5453;
  return x - Math.floor(x);
}

const shorten = (q: string) => (q.length > 72 ? `${q.slice(0, 70).trimEnd()}…` : q).replace(/\?$/, "");

/** Sample climate for a basket. Stands in for the classifier's output. */
export async function getClimate(_basketId: number, positions: Position[]): Promise<Climate> {
  const now = Date.now();
  const signals: ClimateSignal[] = positions.slice(0, 8).flatMap((p, i) => {
    const r = noise(p.market.id);
    if (r < 0.2) return [];
    const sentiment = Math.round((noise(p.market.id + 1) * 2 - 1) * 100) / 100;
    const verbs = sentiment >= 0 ? LEANS_YES : LEANS_NO;
    // Some stories touch the next market too, as real news often moves several at once.
    const next = positions[i + 1];
    return [
      {
        id: `s${p.market.id}`,
        headline: `${verbs[Math.floor(r * verbs.length)]}: ${shorten(p.market.question)}`,
        source: SOURCES[Math.floor(noise(p.market.id + 2) * SOURCES.length)],
        url: null,
        published_at: new Date(now - (i * 2.7 + r * 3) * 3600_000).toISOString(),
        topic: p.market.category || "General",
        sentiment,
        confidence: 0.55 + noise(p.market.id + 3) * 0.4,
        market_ids: next && r > 0.7 ? [p.market.id, next.market.id] : [p.market.id],
      },
    ];
  });

  const byTopic = new Map<string, { sum: number; weight: number; count: number }>();
  for (const s of signals) {
    const t = byTopic.get(s.topic) ?? { sum: 0, weight: 0, count: 0 };
    byTopic.set(s.topic, { sum: t.sum + s.sentiment * s.confidence, weight: t.weight + s.confidence, count: t.count + 1 });
  }
  const topics = [...byTopic]
    .map(([topic, t]) => ({ topic, score: t.sum / t.weight, count: t.count }))
    .sort((a, b) => Math.abs(b.score) - Math.abs(a.score));

  const intensity = signals.length
    ? Math.min(1, signals.reduce((s, x) => s + Math.abs(x.sentiment) * x.confidence, 0) / signals.length + 0.15)
    : 0;
  const regime: Regime = intensity > 0.6 ? "turbulent" : intensity > 0.3 ? "shifting" : "calm";
  const lead = topics[0];

  return {
    regime,
    intensity,
    summary: lead
      ? `${lead.topic} news is leaning ${lead.score >= 0 ? "toward Yes" : "toward No"} across ${lead.count} ${lead.count === 1 ? "story" : "stories"}; ${signals.length} signals in the last day touch this basket.`
      : "No news in the last day touches this basket's markets.",
    as_of: new Date(now).toISOString(),
    topics,
    signals,
  };
}
