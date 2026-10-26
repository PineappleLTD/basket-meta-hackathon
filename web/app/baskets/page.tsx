"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { UnauthorizedError } from "@/lib/auth";
import { BASE_NAV, listBaskets, type Basket } from "@/lib/baskets";
import { date, errorText, signedPct } from "@/lib/format";

export default function Page() {
  const router = useRouter();
  const [baskets, setBaskets] = useState<Basket[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listBaskets()
      .then(setBaskets)
      .catch((err) => (err instanceof UnauthorizedError ? router.replace("/login") : setError(errorText(err))));
  }, [router]);

  return (
    <main className="app">
      <AppHeader />
      <h1 className="app-title">my baskets</h1>

      {error && (
        <p className="thesis-note auth-error" role="alert">
          {error}
        </p>
      )}
      {!baskets && !error && <p className="thesis-note">Loading…</p>}
      {baskets?.length === 0 && (
        <p className="thesis-note">
          No baskets yet. <Link href="/build" className="text-link">Build one from a thesis</Link>.
        </p>
      )}

      {baskets && baskets.length > 0 && (
        <ul className="basket-list">
          {baskets.map((b) => {
            const change = b.nav / BASE_NAV - 1;
            return (
              <li key={b.id}>
                <Link href={`/baskets/${b.id}`}>
                  <span className="basket-name">{b.name}</span>
                  <span className={`mono basket-change ${change < 0 ? "down" : ""}`}>{signedPct(change)}</span>
                  <span className="mono basket-meta">
                    {b.positions.length} positions · {date(b.created_at)}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </main>
  );
}
