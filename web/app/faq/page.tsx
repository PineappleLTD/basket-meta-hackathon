import type { Metadata } from "next";
import Link from "next/link";
import { AuthNav } from "@/components/AuthNav";
import { DbStatus } from "@/components/DbStatus";
import { Faq } from "@/components/Faq";
import { Logo } from "@/components/Logo";

export const metadata: Metadata = { title: "FAQ · basket" };

export default function Page() {
  return (
    <main className="faq-page">
      <nav className="topbar">
        <div className="brand">
          <Link href="/" className="mark">
            <Logo />
            basket
          </Link>
          <DbStatus />
        </div>
        <AuthNav />
      </nav>
      <Faq />
    </main>
  );
}
