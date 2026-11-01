import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { Footer } from "@/components/Footer";
import { FooterGate } from "@/components/FooterGate";
import { SIDEBAR_SCRIPT } from "@/lib/sidebar";
import { THEME_SCRIPT } from "@/lib/theme";
import "./globals.css";

const sans = Inter({ subsets: ["latin"], variable: "--font-sans" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "basket",
  description: "Thematic baskets of prediction-market contracts.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // The head scripts set data-theme and --sidebar-w before React hydrates, so the attributes are expected to differ.
    <html lang="en" className={`${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
        <script dangerouslySetInnerHTML={{ __html: SIDEBAR_SCRIPT }} />
      </head>
      <body>
        {children}
        <FooterGate>
          <Footer />
        </FooterGate>
      </body>
    </html>
  );
}
