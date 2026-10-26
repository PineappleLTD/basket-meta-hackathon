"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { AuthNav } from "@/components/AuthNav";
import { DbStatus } from "@/components/DbStatus";
import { getToken } from "@/lib/auth";

// Top bar for signed-in pages. Sends anyone without a token to log in.
export function AppHeader() {
  const router = useRouter();

  useEffect(() => {
    if (!getToken()) router.replace("/login");
  }, [router]);

  return (
    <nav className="topbar">
      <div className="brand">
        <Link href="/" className="mark">
          <i />
          basket
        </Link>
        <DbStatus />
      </div>
      <AuthNav />
    </nav>
  );
}
