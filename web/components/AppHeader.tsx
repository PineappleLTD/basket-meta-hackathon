"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { AuthNav } from "@/components/AuthNav";
import { DbStatus } from "@/components/DbStatus";
import { getToken } from "@/lib/auth";
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
      <AuthNav />
    </nav>
  );
}
