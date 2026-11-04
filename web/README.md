# Web

Next.js front end for Basket. Sign in, type a thesis, and get weighted Kalshi picks that
express it, chosen by the API's `POST /theses/select`. Save the picks as a basket to track it
under "my baskets".

## Run

Needs Node 20+ and the API running (see `api/README.md`).

```bash
cd web
npm install
npm run dev          # http://localhost:3000
```

The API URL defaults to `/api`; Next.js proxies these requests to the backend on
`127.0.0.1:8000`. Both local and hosted browsers use the website's own origin.
For a separately hosted API, set `NEXT_PUBLIC_API_URL` before building and allow
the website's origin in the API's `PMETF_CORS_ORIGINS` setting.

`npm run build && npm start` serves the production build; `npm run typecheck` runs `tsc`.

## Layout

- `app/page.tsx` – landing page.
- `app/login/`, `app/register/` – both render `components/AuthForm.tsx`.
- `app/basket/page.tsx` – thesis box and picks, with "save basket" (`POST /baskets`; a repeated
  thesis is saved as "name (2)" since names are unique per user).
- `app/baskets/page.tsx`, `app/baskets/[id]/page.tsx` – saved baskets, and one basket's NAV and
  positions, with delete.
- `app/globals.css` – all styling; palette tokens are at the top.
- `components/AppHeader.tsx` – top bar for signed-in pages; redirects to `/login` without a token.
- `components/AuthNav.tsx` – top-right log in / register, or app links and log out.
- `components/MarketGraph.tsx` – the landing page's animated market graph.
- `lib/auth.ts` – sign up, log in, log out, `getMe`, `authFetch` and `apiJson`. The bearer token is kept
  in `localStorage`.
- `lib/theses.ts`, `lib/baskets.ts` – API calls and response types.
- `lib/format.ts` – percent, cents, date and error-message helpers.
