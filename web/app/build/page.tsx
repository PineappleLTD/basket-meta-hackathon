import Form from "next/form";
import Link from "next/link";
import { ACCOUNT_BALANCE, BUILD_ETA_SECONDS, DEFAULT_THEME, SUGGESTED_THESES } from "@/lib/data";
import { usd } from "@/lib/format";

type Props = { searchParams: Promise<{ theme?: string | string[] }> };

const TABS: [label: string, href: string][] = [
  ["Build", "/build"],
  ["Baskets", "/basket"],
  ["Signals", "/basket#signals"],
];

// Picking a chip sets ?theme=... here; submitting hands the thesis to /basket to build.
export default async function BuildPage({ searchParams }: Props) {
  const { theme: param } = await searchParams;
  const theme = (Array.isArray(param) ? param[0] : param)?.trim() || DEFAULT_THEME;

  return (
    <div className="build">
      <nav className="topbar has-tabs">
        <Link href="/" className="mark">
          <i />
          Basket
        </Link>
        <div className="tabs">
          {TABS.map(([label, href]) => (
            <Link key={label} href={href} aria-current={href === "/build" ? "page" : undefined}>
              {label}
            </Link>
          ))}
        </div>
        <span className="bd-bal">{usd(ACCOUNT_BALANCE)}</span>
      </nav>

      <main className="bd-main">
        <div className="bd-kicker">New basket</div>
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
