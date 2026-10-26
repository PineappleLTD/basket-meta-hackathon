import type { Contract, NewsSignal, Side, Venue } from "./types";

export const REBALANCE_THRESHOLD = 0.03;
const MAX_WEIGHT = 0.3;

// [ticker, question, venue, side, price, fair, liquidity, relevance]
type Row = [string, string, Venue, Side, number, number, number, number];

const THEMES: Record<string, { keywords: string[]; contracts: Row[] }> = {
  "AI regulation": {
    keywords: ["ai", "artificial", "intelligence", "regulation", "safety", "chips", "llm", "tech"],
    contracts: [
      ["AIBILL-26", "Will Congress pass a federal AI safety bill in 2026?", "Kalshi", "Yes", 0.12, 0.15, 1_850_000, 1.0],
      ["EUAIACT-DELAY", "Will the EU delay AI Act high-risk obligations before Aug 2027?", "Polymarket", "Yes", 0.41, 0.47, 3_200_000, 0.95],
      ["CAFRONTIER-27", "Will California enact a new frontier-model safety law in 2027?", "Polymarket", "Yes", 0.46, 0.49, 920_000, 0.9],
      ["CHIPEXPORT-Q1", "Will the US tighten AI chip export controls by Mar 31, 2027?", "Kalshi", "Yes", 0.57, 0.6, 4_100_000, 0.85],
      ["AIPREEMPT-26", "Will federal preemption of state AI laws become law in 2026?", "Kalshi", "No", 0.83, 0.86, 1_300_000, 0.8],
      ["FTCAI-26", "Will the FTC sue a major AI developer before 2027?", "Polymarket", "Yes", 0.19, 0.23, 640_000, 0.7],
    ],
  },
  "US recession 2027": {
    keywords: ["recession", "economy", "economic", "gdp", "unemployment", "jobs", "fed", "rates", "inflation", "macro", "slowdown"],
    contracts: [
      ["NBER-REC27", "Will NBER date a US recession beginning in 2027?", "Polymarket", "Yes", 0.27, 0.31, 6_800_000, 1.0],
      ["UNRATE-5PCT", "Will US unemployment hit 5.0% in any month of 2027?", "Kalshi", "Yes", 0.33, 0.38, 2_900_000, 0.95],
      ["GDP-Q127-NEG", "Will Q1 2027 real GDP growth print negative?", "Kalshi", "Yes", 0.21, 0.24, 1_700_000, 0.9],
      ["FEDCUT-DEC26", "Will the Fed cut rates at the December 2026 FOMC?", "Kalshi", "Yes", 0.62, 0.66, 12_500_000, 0.75],
      ["SPX-6000-26", "Will the S&P 500 close 2026 below 6,000?", "Polymarket", "Yes", 0.11, 0.12, 3_600_000, 0.7],
      ["CPI-DEC26-3", "Will December 2026 CPI come in above 3.0% YoY?", "Kalshi", "No", 0.58, 0.61, 2_200_000, 0.65],
    ],
  },
  "Midterms 2026": {
    keywords: ["midterm", "midterms", "election", "elections", "house", "senate", "congress", "democrats", "republicans", "vote", "politics"],
    contracts: [
      ["HOUSE-DEM-26", "Will Democrats win control of the House in 2026?", "Polymarket", "Yes", 0.66, 0.68, 48_000_000, 1.0],
      ["SENATE-GOP-26", "Will Republicans hold the Senate after the 2026 midterms?", "Kalshi", "Yes", 0.72, 0.73, 31_000_000, 0.95],
      ["SPLIT-GOV-27", "Will 2027 begin with divided control of Congress?", "Polymarket", "Yes", 0.58, 0.6, 7_900_000, 0.9],
      ["HOUSE-POPV-D3", "Will Democrats win the House popular vote by 3+ points?", "Polymarket", "Yes", 0.47, 0.5, 5_300_000, 0.85],
      ["TURNOUT-50", "Will 2026 midterm turnout exceed 50% of eligible voters?", "Kalshi", "Yes", 0.39, 0.44, 1_600_000, 0.8],
    ],
  },
  "Crypto adoption": {
    keywords: ["crypto", "bitcoin", "btc", "ethereum", "eth", "stablecoin", "stablecoins", "defi", "blockchain", "digital"],
    contracts: [
      ["BTC-150K-Q1", "Will Bitcoin trade above $150,000 before Apr 1, 2027?", "Polymarket", "Yes", 0.23, 0.26, 9_400_000, 1.0],
      ["CLARITY-26", "Will a crypto market-structure bill become law in 2026?", "Kalshi", "Yes", 0.29, 0.34, 2_100_000, 0.95],
      ["STABLE-400B", "Will stablecoin supply exceed $400B by Dec 31, 2026?", "Kalshi", "Yes", 0.52, 0.55, 1_150_000, 0.85],
      ["ETHBTC-Q4", "Will ETH outperform BTC in Q4 2026?", "Polymarket", "Yes", 0.44, 0.45, 2_700_000, 0.75],
      ["G7-BTCRES", "Will a G7 government announce a Bitcoin reserve by 2027?", "Polymarket", "Yes", 0.08, 0.11, 780_000, 0.7],
    ],
  },
  "Climate & energy": {
    keywords: ["climate", "energy", "oil", "heat", "weather", "carbon", "ev", "evs", "emissions", "green", "solar", "warming"],
    contracts: [
      ["HOTYEAR-26", "Will 2026 rank as the hottest year on record (NASA)?", "Kalshi", "Yes", 0.37, 0.42, 1_400_000, 1.0],
      ["COP31-PHASE", "Will COP31 end with a fossil-fuel transition pledge?", "Polymarket", "Yes", 0.24, 0.29, 860_000, 0.9],
      ["EVSHARE-Q4", "Will US EV share of new car sales top 10% in Q4 2026?", "Polymarket", "Yes", 0.31, 0.33, 540_000, 0.8],
      ["BRENT-80-26", "Will Brent crude settle above $80 on Dec 31, 2026?", "Kalshi", "No", 0.76, 0.78, 3_800_000, 0.75],
      ["ELNINO-H127", "Will NOAA declare El Niño conditions before July 2027?", "Kalshi", "Yes", 0.34, 0.36, 450_000, 0.7],
    ],
  },
};

export const EXAMPLE_THEMES = Object.keys(THEMES);
export const DEFAULT_THEME = EXAMPLE_THEMES[0];

/** Starter theses on the Build page. Ones without a theme of their own get the broad basket. */
export const SUGGESTED_THESES = [
  "AI regulation",
  "US recession 2027",
  "Fed cuts by March",
  "Oil above $100",
  "Bitcoin above $150k",
  "Atlantic hurricane season",
  "SpaceX IPO in 2027",
  "Housing slowdown",
];

/** Cash available to the signed-in account, in USD. */
export const ACCOUNT_BALANCE = 2500;

/** Typical time to match and weight a basket. */
export const BUILD_ETA_SECONDS = 8;

// [minutesAgo, source, headline, ticker, predicted move, actual move]
const HEADLINES: [number, string, string, string, number, number][] = [
  [14, "Wire", "Senate Commerce schedules markup of bipartisan AI safety framework", "AIBILL-26", 0.05, 0.01],
  [52, "Agency", "EU Commission circulates draft to pause parts of the AI Act", "EUAIACT-DELAY", 0.07, 0.02],
  [180, "Filing", "Commerce Department notice previews wider controls on AI accelerators", "CHIPEXPORT-Q1", 0.04, 0.035],
  [300, "Wire", "California session closes without a vote on revised frontier-model bill", "CAFRONTIER-27", -0.05, -0.045],
  [420, "Agency", "FTC issues information requests to five AI companies", "FTCAI-26", 0.03, 0.025],
  [9, "Data", "Payrolls miss forecasts as unemployment ticks up to 4.6%", "UNRATE-5PCT", 0.05, 0.015],
  [38, "Wire", "Fed officials signal openness to a December cut", "FEDCUT-DEC26", 0.06, 0.055],
  [95, "Data", "ISM manufacturing contracts for a seventh straight month", "GDP-Q127-NEG", 0.03, 0.01],
  [240, "Data", "Core inflation cools more than expected in September", "CPI-DEC26-3", 0.04, 0.035],
  [360, "Wire", "Leading economic index falls for a fourth straight month", "NBER-REC27", 0.04, 0.005],
  [6, "Poll", "National generic-ballot average widens to D+4", "HOUSE-POPV-D3", 0.06, 0.02],
  [61, "Wire", "Early-vote returns run ahead of 2022 pace in key states", "TURNOUT-50", 0.05, 0.01],
  [130, "Poll", "Senate battleground polls tighten in the final stretch", "SENATE-GOP-26", -0.03, -0.025],
  [210, "Wire", "Ratings shift three toss-up House seats toward Democrats", "HOUSE-DEM-26", 0.03, 0.025],
  [18, "Exchange", "Spot bitcoin ETFs log their largest weekly inflow of the year", "BTC-150K-Q1", 0.04, 0.035],
  [75, "Wire", "Senate Banking sets a floor timeline for market-structure bill", "CLARITY-26", 0.06, 0.015],
  [160, "Filing", "Payments network expands stablecoin settlement to six markets", "STABLE-400B", 0.03, 0.01],
  [400, "Wire", "Finance ministry denies report of a planned bitcoin reserve", "G7-BTCRES", -0.04, -0.005],
  [27, "Agency", "October global temperature anomaly on track for a record", "HOTYEAR-26", 0.05, 0.015],
  [110, "Wire", "OPEC+ agrees to extend output increases into Q1", "BRENT-80-26", 0.03, 0.025],
  [230, "Wire", "COP31 draft text drops fossil-fuel transition language", "COP31-PHASE", -0.06, -0.02],
  [350, "Data", "US EV sales hit a quarterly record in Q3", "EVSHARE-Q4", 0.03, 0.02],
  [500, "Agency", "NOAA raises odds of El Niño developing next spring", "ELNINO-H127", 0.04, 0.03],
];

function matchTheme(theme: string): string | null {
  const words = new Set(theme.toLowerCase().match(/[a-z0-9]+/g) ?? []);
  let best: string | null = null;
  let bestScore = 0;
  for (const [name, spec] of Object.entries(THEMES)) {
    const score = spec.keywords.filter((k) => words.has(k)).length;
    if (score > bestScore) [best, bestScore] = [name, score];
  }
  return best;
}

/** Relevance × liquidity^0.25, capped at MAX_WEIGHT with the excess redistributed. */
function weights(rows: Row[]): number[] {
  const raw = rows.map((r) => r[7] * r[6] ** 0.25);
  const total = raw.reduce((a, b) => a + b, 0);
  const w = raw.map((v) => v / total);
  for (let i = 0; i < w.length; i++) {
    const over = w.map((v) => v > MAX_WEIGHT);
    if (!over.includes(true)) break;
    const excess = w.reduce((sum, v, j) => sum + (over[j] ? v - MAX_WEIGHT : 0), 0);
    const rest = w.reduce((sum, v, j) => sum + (over[j] ? 0 : v), 0);
    w.forEach((v, j) => (w[j] = over[j] ? MAX_WEIGHT : v + (excess * v) / rest));
  }
  return w;
}

/** Contracts for a theme, sorted by weight. Unmatched themes get a broad basket of the
 *  most liquid contract from each theme. */
export async function buildBasket(theme: string): Promise<Contract[]> {
  const name = matchTheme(theme);
  const rows = name
    ? THEMES[name].contracts
    : Object.values(THEMES).map((spec) => spec.contracts.reduce((a, b) => (b[6] > a[6] ? b : a)));
  const w = weights(rows);
  return rows
    .map(([ticker, question, venue, side, price, fair, liquidity], i) => ({
      ticker, question, venue, side, price, fair, liquidity, weight: w[i],
    }))
    .sort((a, b) => b.weight - a.weight);
}

/** Headlines touching the basket, newest first, flagged where the predicted move and the
 *  market's actual move differ by at least `threshold`. */
export async function getNewsSignals(basket: Contract[], threshold = REBALANCE_THRESHOLD): Promise<NewsSignal[]> {
  const byTicker = new Map(basket.map((c) => [c.ticker, c]));
  return HEADLINES.filter(([, , , ticker]) => byTicker.has(ticker))
    .map(([minutesAgo, source, headline, ticker, predicted, actual]) => {
      const gap = Math.round((predicted - actual) * 1e4) / 1e4;
      return {
        minutesAgo, source, headline, ticker, predicted, actual, gap,
        question: byTicker.get(ticker)!.question,
        rebalance: Math.abs(gap) >= threshold,
      };
    })
    .sort((a, b) => a.minutesAgo - b.minutesAgo);
}
