import Link from "next/link";

type LinkGroup = [heading: string, links: [label: string, href: string][]];

// Every footer link lives here.
const LINKS: LinkGroup[] = [
  ["Product", [["Benchmarks", "/benchmarks"]]],
  ["Learn", [["FAQ", "/faq"]]],
];

export function Footer() {
  return (
    <footer className="sf">
      <div className="sf-in">
        <div className="sf-main">
          <nav aria-label="Footer" className="sf-nav">
            {LINKS.map(([heading, links]) => (
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

        <div className="sf-bar">
          <span className="sf-copy">© {new Date().getFullYear()} Basket</span>
        </div>
      </div>
    </footer>
  );
}
