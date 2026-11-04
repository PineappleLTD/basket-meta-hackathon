import { apiJson } from "./auth";
import type { Side } from "./theses";

export type Regime = "calm" | "shifting" | "turbulent";
export type ClimateMode = "off" | "suggest" | "auto";
export interface ClimateSettings { mode: ClimateMode; sensitivity: number; max_shift: number }
export const DEFAULT_CLIMATE_SETTINGS: ClimateSettings = { mode: "suggest", sensitivity: 0.5, max_shift: 0.05 };
export interface Shift {
  market_id: number; side: Side; current_weight: number; proposed_weight: number;
  pressure: number; signal_ids: string[];
}
export interface ClimateSignal {
  id: string; headline: string; source: string; url: string | null; published_at: string;
  topic: string; sentiment: number; confidence: number; market_ids: number[];
  classification: "ADVERSE" | "FAVORABLE" | "NEUTRAL" | "ABSTAIN" | "UNRELATED";
  reason: string | null; experimental: boolean;
}
export interface TopicPressure { topic: string; score: number; count: number }
export interface NewsCycle {
  id: string; status: string; note: string | null; updated_at: string;
  sells: { market_id: number; side: Side; count: number; filled: number }[];
  buys: { market_id: number; side: Side; count: number; filled: number }[];
  proceeds_cents: number; spent_cents: number;
}
export interface NewsMonitoring {
  status: string; note: string; checked_at: string | null; interval_seconds: number;
  requests: Record<"discovery" | "evidence", { used: number; limit: number }>;
}
export interface Climate {
  regime: Regime; intensity: number; summary: string; as_of: string;
  topics: TopicPressure[]; signals: ClimateSignal[]; settings: ClimateSettings;
  proposal: { id: string; shifts: Shift[]; reason: string | null };
  cycle: NewsCycle | null; basket_cash: number; missing_rules: string[]; execution_mode: "paper";
  monitoring?: NewsMonitoring;
}
export const turnover = (shifts: Shift[]) =>
  shifts.reduce((sum, shift) => sum + Math.abs(shift.proposed_weight - shift.current_weight), 0) / 2;
export const getClimate = (basketId: number) => apiJson<Climate>(`/baskets/${basketId}/climate`);
export const saveClimateSettings = (basketId: number, settings: ClimateSettings) =>
  apiJson<ClimateSettings>(`/baskets/${basketId}/climate/settings`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings),
  });
export const applyShifts = (basketId: number, expectedId: string) =>
  apiJson<NewsCycle>(`/baskets/${basketId}/climate/apply`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_id: expectedId }),
  });
export const reviewStoppedCycle = (basketId: number, cycleId: string) =>
  apiJson<NewsCycle>(`/baskets/${basketId}/climate/review`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cycle_id: cycleId }),
  });
