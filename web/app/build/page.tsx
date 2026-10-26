import Form from "next/form";
import Link from "next/link";
import { AppHeader } from "@/components/AppHeader";
import { ACCOUNT_BALANCE } from "@/lib/accounts";
import { usd } from "@/lib/format";
import { BUILD_ETA_SECONDS, DEFAULT_THEME, SUGGESTED_THESES } from "@/lib/theses";

type Props = { searchParams: Promise<{ theme?: string | string[] }> };

// Picking a chip sets ?theme=... here; submitting hands the thesis to /basket to build.
export default async function BuildPage({ searchParams }: Props) {
  const { theme: param } = await searchParams;
  const theme = (Array.isArray(param) ? param[0] : param)?.trim() || DEFAULT_THEME;

  return (
    <div className="build">
      {/* Open to everyone; /basket asks for a login when the thesis is submitted. */}
      <AppHeader requireAuth={false} />

      <main className="bd-main">
        <div className="bd-kicker">
          <span>New basket</span>
          <span className="bd-bal">{usd(ACCOUNT_BALANCE)}</span>
        </div>
        <h1>Your thesis, as a portfolio.</h1>

        <Form action="/basket">
          <label htmlFor="thesis" className="bd-label">
            Theme or thesis
          </label>
          <div className="bd-field">
            <input
              key={theme}
              id="thesis"
              name="theme"
              defaultValue={theme}
              placeholder="Describe a trend or thesis"
              autoComplete="off"
            />
            <button type="submit">
              Build basket <span aria-hidden="true">→</span>
            </button>
          </div>
        </Form>

        <div className="bd-chips">
          {SUGGESTED_THESES.map((t) => (
            <Link key={t} href={{ query: { theme: t } }} scroll={false} className="bd-chip" aria-current={t === theme || undefined}>
              {t}
            </Link>
          ))}
        </div>

        <footer className="bd-foot">
          <p>Basket matches open Polymarket and Kalshi contracts, then weights them by relevance, liquidity and overlap.</p>
          <span className="mono">~ {BUILD_ETA_SECONDS} sec</span>
        </footer>
      </main>
    </div>
  );
}
