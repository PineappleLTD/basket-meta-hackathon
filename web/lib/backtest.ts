// Snapshot of `api/backtest.py --json`: 29 theses bought 2026-09-19 and held to 2026-11-03.
// Re-run the backtest and regenerate this file to update the benchmarks page.

export const BACKTEST_WINDOW = { from: "2026-09-19", to: "2026-11-03", days: 45 };

export interface ThesisResult {
  thesis: string;
  // Markets in the basket.
  markets: number;
  // Net returns after entry fees and spread. `single` is everything in the basket's most traded
  // market: the one bet someone would most likely find on their own.
  basket: number;
  single: number;
  // Largest peak-to-trough fall while held.
  basketDrawdown: number;
  singleDrawdown: number;
}

export const BACKTEST: ThesisResult[] = [
  { thesis: "Fed Double Cut Path", markets: 10, basket: -0.1038, single: -0.4033, basketDrawdown: 0.1142, singleDrawdown: 0.4687 },
  { thesis: "Rate cuts and a soft landing", markets: 7, basket: -0.3648, single: -0.7230, basketDrawdown: 0.2098, singleDrawdown: 0.3952 },
  { thesis: "LeBron's Final Season", markets: 7, basket: -0.0424, single: -0.0171, basketDrawdown: 0.0237, singleDrawdown: 0.0103 },
  { thesis: "Sixers Collapse Celtics Rise", markets: 7, basket: -0.0469, single: -0.0560, basketDrawdown: 0.0511, singleDrawdown: 0.0442 },
  { thesis: "President JD Vance", markets: 10, basket: -0.0334, single: -0.2831, basketDrawdown: 0.0197, singleDrawdown: 0.2418 },
  { thesis: "Google Bull Run", markets: 10, basket: 0.0890, single: 0.0225, basketDrawdown: 0.0199, singleDrawdown: 0.0157 },
  { thesis: "Inflation will stay stubbornly above 3% into next year", markets: 10, basket: -0.0127, single: 0.0056, basketDrawdown: 0.0646, singleDrawdown: 0.0309 },
  { thesis: "The Fed will cut rates aggressively as the labor market weakens", markets: 9, basket: -0.0177, single: -0.4033, basketDrawdown: 0.1228, singleDrawdown: 0.4687 },
  { thesis: "The US economy avoids a recession and keeps growing steadily", markets: 9, basket: -0.0352, single: 0.0051, basketDrawdown: 0.0905, singleDrawdown: 0.0727 },
  { thesis: "A recession hits the US in 2027", markets: 9, basket: -0.1080, single: -0.1860, basketDrawdown: 0.1590, singleDrawdown: 0.2979 },
  { thesis: "Oil prices will spike because of Middle East conflict", markets: 4, basket: -0.1544, single: 0.0366, basketDrawdown: 0.1646, singleDrawdown: 0.0743 },
  { thesis: "Gold keeps hitting record highs as investors flee the dollar", markets: 7, basket: -0.0485, single: -0.4094, basketDrawdown: 0.1806, singleDrawdown: 0.5143 },
  { thesis: "Bitcoin breaks above $150k as crypto adoption accelerates", markets: 7, basket: -0.3068, single: 0.0420, basketDrawdown: 0.3599, singleDrawdown: 0.0595 },
  { thesis: "The crypto market crashes and Bitcoin falls below $80k", markets: 5, basket: -0.1559, single: -0.4455, basketDrawdown: 0.2546, singleDrawdown: 0.4444 },
  { thesis: "Democrats take back the House in the 2026 midterms", markets: 9, basket: 0.0488, single: 0.0370, basketDrawdown: 0.0606, singleDrawdown: 0.0256 },
  { thesis: "Republicans keep control of both chambers of Congress", markets: 6, basket: -0.3437, single: -0.7105, basketDrawdown: 0.3068, singleDrawdown: 0.4714 },
  { thesis: "Trump's approval rating keeps falling", markets: 3, basket: 0.5083, single: 0.9631, basketDrawdown: 0.1316, singleDrawdown: 0.1627 },
  { thesis: "Tariffs on China escalate further", markets: 5, basket: -0.1260, single: -0.0637, basketDrawdown: 0.1369, singleDrawdown: 0.0643 },
  { thesis: "The war in Ukraine reaches a ceasefire", markets: 7, basket: -0.2191, single: -0.5744, basketDrawdown: 0.2859, singleDrawdown: 0.6258 },
  { thesis: "OpenAI keeps its lead in AI and releases GPT-6", markets: 8, basket: -0.1848, single: -0.0938, basketDrawdown: 0.2163, singleDrawdown: 0.2993 },
  { thesis: "Nvidia remains the most valuable company in the world", markets: 5, basket: 0.0544, single: 0.0125, basketDrawdown: 0.0602, singleDrawdown: 0.0105 },
  { thesis: "Tesla struggles as EV demand slows", markets: 9, basket: -0.0667, single: -0.0548, basketDrawdown: 0.0664, singleDrawdown: 0.2124 },
  { thesis: "The S&P 500 finishes the year at a record high", markets: 7, basket: 0.0106, single: -0.0283, basketDrawdown: 0.0782, singleDrawdown: 0.0457 },
  { thesis: "This will be one of the hottest years on record", markets: 5, basket: -0.2104, single: 0.0515, basketDrawdown: 0.2594, singleDrawdown: 0.3387 },
  { thesis: "SpaceX Starship reaches orbit and starts regular flights", markets: 8, basket: -0.0920, single: -0.3430, basketDrawdown: 0.1314, singleDrawdown: 0.4902 },
  { thesis: "Taylor Swift dominates the charts and award shows", markets: 10, basket: -0.0183, single: -0.2516, basketDrawdown: 0.2172, singleDrawdown: 0.4500 },
  { thesis: "A big-budget movie flops at the box office this year", markets: 5, basket: -0.0246, single: -0.0277, basketDrawdown: 0.1041, singleDrawdown: 0.0284 },
  { thesis: "Elon Musk steps back from running his companies", markets: 9, basket: -0.0471, single: -0.0548, basketDrawdown: 0.0938, singleDrawdown: 0.2124 },
  { thesis: "Housing prices fall as mortgage rates stay high", markets: 7, basket: 0.1835, single: -0.1860, basketDrawdown: 0.1126, singleDrawdown: 0.2979 },
];

export function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

export function stdev(xs: number[]): number {
  const mean = xs.reduce((a, b) => a + b, 0) / xs.length;
  return Math.sqrt(xs.reduce((a, b) => a + (b - mean) ** 2, 0) / (xs.length - 1));
}

/** "+12.3%", "−4.7%" (a true minus sign). */
export function signedPct(v: number, digits = 1): string {
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(digits)}%`;
}
