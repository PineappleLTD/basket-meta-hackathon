"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useBaskets } from "@/components/BasketsContext";
import { getMe, UnauthorizedError, type User } from "@/lib/auth";
import { getAccount, resetAccount, type Account } from "@/lib/accounts";
import { errorText, signedPct, usd } from "@/lib/format";

// Settings for the whole account, not one basket: who's signed in, the balance, and a reset.
export default function Page() {
  const router = useRouter();
  const { refresh } = useBaskets();
  const [user, setUser] = useState<User | null>(null);
  const [account, setAccount] = useState<Account | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);

  function fail(err: unknown) {
    if (err instanceof UnauthorizedError) router.replace("/login");
    else setError(errorText(err));
  }

  useEffect(() => {
    getMe().then(setUser);
    getAccount().then(setAccount).catch(fail);
  }, []);

  async function reset() {
    if (!confirm("Reset your paper account? Every holding, trade and automation in every basket is cleared, and cash goes back to the start.")) return;
    setResetting(true);
    setError(null);
    try {
      setAccount(await resetAccount());
      refresh();
    } catch (err) {
      fail(err);
    } finally {
      setResetting(false);
    }
  }

  const change = account ? account.pnl / account.starting_cash : 0;

  return (
    <div className="bd">
      <div className="bd-in">
        <header className="bd-head">
          <p className="bd-meta">{user?.email ?? " "}</p>
          <h1 className="bd-title">Settings</h1>
        </header>

        {error && (
          <p className="bd-status auth-error" role="alert">
            {error}
          </p>
        )}

        {account && (
          <section className="bd-section" aria-label="Account">
            <h2>Account</h2>
            <dl className="stats">
              <div>
                <dt>Equity</dt>
                <dd>{usd(account.equity)}</dd>
              </div>
              <div>
                <dt>Cash</dt>
                <dd>{usd(account.cash)}</dd>
              </div>
              <div>
                <dt>Return</dt>
                <dd className={change < 0 ? "down" : "up"}>{signedPct(change, 2)}</dd>
              </div>
              <div>
                <dt>Starting cash</dt>
                <dd>{usd(account.starting_cash)}</dd>
              </div>
            </dl>
          </section>
        )}

        {account?.mode === "paper" && (
          <section className="bd-section">
            <h2>Reset account</h2>
            <p className="bd-foot">
              Clears every holding, trade and automation in every basket, and puts cash back to{" "}
              {usd(account.starting_cash)}. Your baskets themselves are kept.{" "}
              <button type="button" className="text-btn" onClick={reset} disabled={resetting}>
                {resetting ? "Resetting…" : "Reset account"}
              </button>
            </p>
          </section>
        )}
      </div>
    </div>
  );
}
