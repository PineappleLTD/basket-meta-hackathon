"use client";

import { usePathname } from "next/navigation";

// The root layout renders the footer on every route except the app: the /basket thesis chat and the /baskets pages.
export function FooterGate({ children }: { children: React.ReactNode }) {
  return usePathname().startsWith("/basket") ? null : children;
}
