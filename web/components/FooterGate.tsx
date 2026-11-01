"use client";

import { usePathname } from "next/navigation";

// The root layout renders the footer on every route except the /basket thesis chat.
export function FooterGate({ children }: { children: React.ReactNode }) {
  return usePathname() === "/basket" ? null : children;
}
