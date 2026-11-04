"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { BasketName } from "@/components/BasketName";
import { useBaskets } from "@/components/BasketsContext";
import { SidebarResizer } from "@/components/SidebarResizer";
import { ARBITRAGE_ENABLED } from "@/lib/arbitrage";
import { BASE_NAV } from "@/lib/baskets";
import { signedPct, usd } from "@/lib/format";

// Left rail of the app: start a new basket, or jump to a saved one.
export function BasketSidebar() {
  const pathname = usePathname();
  const { baskets, error, newDraft, titles } = useBaskets();

  return (
    <aside className="sidebar">
      <div className="sidebar-top">
        <button type="button" className="btn new-basket" onClick={newDraft}>
          <span aria-hidden="true">+</span> new basket
        </button>
        {ARBITRAGE_ENABLED && (
          <Link
            href="/arbitrage"
            className={`sidebar-tool${pathname === "/arbitrage" ? " active" : ""}`}
            aria-current={pathname === "/arbitrage" ? "page" : undefined}
          >
            <span>Arbitrage scanner</span>
            <span className="mono">logic gaps</span>
          </Link>
        )}
        <div className="sidebar-head">
          <span className="eyebrow">my baskets</span>
          {baskets && <span className="mono">{baskets.length}</span>}
        </div>
      </div>

      {error && (
        <p className="sidebar-note auth-error" role="alert">
          {error}
        </p>
      )}
      {!baskets && !error && <p className="sidebar-note">Loading…</p>}
      {baskets?.length === 0 && <p className="sidebar-note">Saved baskets show up here.</p>}

      {baskets && baskets.length > 0 && (
        <ul className="sidebar-list">
          {baskets.map((b) => {
            const href = `/baskets/${b.id}`;
            const change = b.nav / BASE_NAV - 1;
            return (
              <li key={b.id}>
                <Link
                  href={href}
                  className={pathname === href ? "active" : undefined}
                  aria-current={pathname === href ? "page" : undefined}
                >
                  <span className="sb-name">
                    <BasketName name={b.name} state={titles[b.id]} />
                  </span>
                  {b.lock ? (
                    <span className="mono sb-change up">+{usd(b.lock.profit)}</span>
                  ) : (
                    <span className={`mono sb-change ${change < 0 ? "down" : "up"}`}>{signedPct(change)}</span>
                  )}
                  <span className="mono sb-meta">
                    {b.lock ? `locked · ${b.lock.sets.toLocaleString()} sets` : `${b.positions.length} positions`}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}

      <SidebarResizer />
    </aside>
  );
}
