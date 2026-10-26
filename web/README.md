# Web

Next.js front end for Basket. Pick a theme and get a weighted basket of Polymarket and Kalshi
contracts, plus a news feed that flags a rebalance when a headline's predicted move and the
market's actual move diverge.

## Run

Needs Node 20+.

```bash
cd web
npm install
npm run dev          # http://localhost:3000
```

`npm run build && npm start` serves the production build; `npm run typecheck` runs `tsc`.

## Layout

- `app/page.tsx` – reads `?theme=`, builds the basket and news signals on the server, renders
  the sections. The theme lives in the URL, so a basket survives refreshes and can be shared.
- `app/globals.css` – all styling; palette tokens are at the top.
- `components/` – `Hero` (with the ASCII texture), `ThemePicker`, `BasketView` (table,
  allocation bars, stats), `NewsFeed`.
- `lib/data.ts` – the data seam: `buildBasket(theme)` and `getNewsSignals(basket)`.
- `lib/mock-data.ts` – mock themes, contracts and headlines behind that seam.
- `lib/ascii.ts` – procedural value-noise texture for the hero.

## Real data

Implement `buildBasket(theme): Promise<Contract[]>` and
`getNewsSignals(basket): Promise<NewsSignal[]>` (types in `lib/types.ts`) and re-export them
from `lib/data.ts`. They run on the server, so they can call `api/` or an LLM directly, keep
keys out of the browser, and aren't subject to CORS.
