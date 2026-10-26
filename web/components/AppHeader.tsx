"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { AuthNav } from "@/components/AuthNav";
import { DbStatus } from "@/components/DbStatus";
import { NavTabs } from "@/components/NavTabs";
import { getToken } from "@/lib/auth";

// Top bar for app pages. Sends anyone without a token to log in, unless the page is public.
export function AppHeader({ requireAuth = true }: { requireAuth?: boolean }) {
  const router = useRouter();

  useEffect(() => {
    if (requireAuth && !getToken()) router.replace("/login");
  }, [requireAuth, router]);

  return (
    <nav className="topbar has-tabs">
      <div className="brand">
        <Link href="/" className="mark">
          <i />
          basket
        </Link>
        <DbStatus />
      </div>
      <NavTabs />
      <AuthNav />
    </nav>
  );
}
