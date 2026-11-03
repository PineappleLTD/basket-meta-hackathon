"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useAccount } from "@/components/AccountContext";
import { AuthNav } from "@/components/AuthNav";
import { DbStatus } from "@/components/DbStatus";
import { getToken } from "@/lib/auth";
import { signedPct, usd } from "@/lib/format";
import { InfoTip } from "@/components/InfoTip";
import { Logo } from "@/components/Logo";

// Top bar for app pages. Sends anyone without a token to log in.
export function AppHeader() {
  const router = useRouter();

  useEffect(() => {
    if (!getToken()) router.replace("/login");
  }, [router]);

  return (
    <nav className="topbar">
      <div className="brand">
        <Link href="/" className="mark">
          <Logo />
          basket
        </Link>
        <span className="mode-badge" title="Paper trading: simulated cash, no real money">Paper</span>
        <DbStatus />
      </div>
      <div className="topbar-end">
        <AccountSummary />
        <AuthNav />
      </div>
    </nav>
  );
}

// The whole paper account at a glance: equity with its change since opening, and cash to trade.
function AccountSummary() {
  const { account } = useAccount();
  if (!account) return null;
  const change = account.pnl / account.starting_cash;
  return (
    <dl className="topbar-account">
      <div>
        <dt>
          Equity
          <InfoTip end>Total account value: cash plus all positions at mid prices. The change is since the account opened.</InfoTip>
        </dt>
        <dd>
          {usd(account.equity)} <span className={change < 0 ? "down" : "up"}>{signedPct(change, 2)}</span>
        </dd>
      </div>
      <div>
        <dt>Buying power</dt>
        <dd>{usd(account.available_cash)}</dd>
      </div>
    </dl>
  );
}
