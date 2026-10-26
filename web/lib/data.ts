// The page only talks to these two functions. Swap the mock implementations for the
// real LLM bundler and news engine (e.g. calls to api/) without touching any component.
export {
  buildBasket, getNewsSignals, ACCOUNT_BALANCE, BUILD_ETA_SECONDS, DEFAULT_THEME, EXAMPLE_THEMES, REBALANCE_THRESHOLD,
  SUGGESTED_THESES,
} from "./mock-data";
