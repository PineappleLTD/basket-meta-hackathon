"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { CATEGORIES, FAQS } from "@/lib/faq";

// Phones type curly apostrophes, so both spellings of "what's" match.
const normalize = (text: string) => text.toLowerCase().replace(/’/g, "'");

export function Faq() {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("All");
  const [open, setOpen] = useState<string | null>(FAQS[0].number);
  const [linked, setLinked] = useState<string | null>(null);

  // A link like /faq#q-07 opens that question.
  useEffect(() => {
    const number = location.hash.slice("#q-".length);
    if (FAQS.some((f) => f.number === number)) {
      setOpen(number);
      setLinked(number);
    }
  }, []);

  // Swapping the open question moves the rows after the browser has scrolled to the hash, so scroll again.
  useEffect(() => {
    if (linked) document.getElementById(`q-${linked}`)?.scrollIntoView();
  }, [linked]);

  const search = normalize(query.trim());
  const matches = FAQS.filter((f) => normalize(`${f.question} ${f.answer}`).includes(search));
  const count = (c: string) => (c === "All" ? matches.length : matches.filter((f) => f.category === c).length);
  const shown = category === "All" ? matches : matches.filter((f) => f.category === category);

  return (
    <div className="faq">
      <aside className="faq-side">
        <div className="eyebrow">FAQ</div>
        <h1 className="faq-title">
          Questions,
          <br />
          answered.
        </h1>

        <label className="faq-search">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <circle cx="7" cy="7" r="4.5" />
            <path d="m10.5 10.5 3 3" />
          </svg>
          <span className="sr-only">Search questions</span>
          <input type="search" placeholder="Search questions" value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>

        <ul className="faq-cats">
          {CATEGORIES.map((c) => (
            <li key={c}>
              <button type="button" aria-pressed={c === category} onClick={() => setCategory(c)}>
                {c}
                <span className="mono">{count(c)}</span>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <div className="faq-main">
        <ul className="faq-list">
          {shown.map((f) => {
            const expanded = open === f.number;
            return (
              <li key={f.number} id={`q-${f.number}`}>
                <h2>
                  <button
                    type="button"
                    className="faq-q"
                    aria-expanded={expanded}
                    aria-controls={`faq-${f.number}`}
                    onClick={() => setOpen(expanded ? null : f.number)}
                  >
                    <span className="faq-n mono">{f.number}</span>
                    <span>{f.question}</span>
                    <span className="faq-toggle" aria-hidden="true">
                      <svg viewBox="0 0 10 10">
                        <path d={expanded ? "M1.5 5h7" : "M1.5 5h7M5 1.5v7"} />
                      </svg>
                    </span>
                  </button>
                </h2>
                <p id={`faq-${f.number}`} className="faq-a" hidden={!expanded}>
                  {f.answer}
                </p>
              </li>
            );
          })}
        </ul>
        {shown.length === 0 && <p className="faq-empty">No questions match your search.</p>}

        <div className="faq-contact">
          <div>
            <h2>Still curious.</h2>
            <p>We usually reply within a day.</p>
          </div>
          <Link href="#" className="cta">
            Contact us <span aria-hidden="true">→</span>
          </Link>
        </div>
      </div>
    </div>
  );
}
