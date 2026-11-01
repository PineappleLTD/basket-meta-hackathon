import Link from "next/link";
import { NewsletterForm } from "./NewsletterForm";

type LinkGroup = [heading: string, links: [label: string, href: string][]];

// Every footer link lives here. Swap "#" for a real route once the page exists.
// The last group is the legal row in the bottom bar; the rest are the link columns.
const LINKS: LinkGroup[] = [
  ["Product", [
    ["Build a basket", "/basket"],
    ["My baskets", "/baskets"],
    ["News & signals", "#"],
    ["Pricing", "#"],
  ]],
  ["Learn", [
    ["How baskets work", "#"],
    ["Rebalancing", "#"],
    ["Venues", "#"],
    ["FAQ", "/faq"],
  ]],
  ["Company", [
    ["About", "#"],
    ["Careers", "#"],
    ["Contact", "#"],
    ["X / Twitter", "#"],
  ]],
  ["Legal", [
    ["Terms", "#"],
    ["Privacy", "#"],
    ["Risk disclosure", "#"],
  ]],
];

const COLUMNS = LINKS.slice(0, -1);
const [, LEGAL] = LINKS[LINKS.length - 1];

export function Footer() {
  return (
    <footer className="sf">
      <div className="sf-in">
        <div className="sf-main">
          <div className="sf-about">
            <Link href="/" className="sf-mark">
              <i />
              Basket
            </Link>
            <p>One thesis a week, bundled into a basket. No spam.</p>
            <NewsletterForm />
          </div>

          <nav aria-label="Footer" className="sf-nav">
            {COLUMNS.map(([heading, links]) => (
              <div key={heading}>
                <h2>{heading}</h2>
                <ul>
                  {links.map(([label, href]) => (
                    <li key={label}>
                      <Link href={href}>{label}</Link>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </nav>
        </div>

        <div className="sf-word" aria-hidden="true">
          Basket<span>.</span>
        </div>

        <div className="sf-bar">
          <span className="sf-copy">© {new Date().getFullYear()} Basket</span>
          <p>
            Contracts trade on their own venues and can lose their full value. Availability varies by jurisdiction.
            Not investment advice.
          </p>
          <nav aria-label="Legal">
            {LEGAL.map(([label, href]) => (
              <Link key={label} href={href}>
                {label}
              </Link>
            ))}
          </nav>
        </div>
      </div>
    </footer>
  );
}
