"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS: [label: string, href: string][] = [["Build", "/build"]];

// Center links in the top bar. Pair with `topbar has-tabs` so they sit between the brand and AuthNav.
export function NavTabs() {
  const pathname = usePathname();
  return (
    <div className="tabs">
      {TABS.map(([label, href]) => (
        <Link key={label} href={href} aria-current={pathname === href ? "page" : undefined}>
          {label}
        </Link>
      ))}
    </div>
  );
}
