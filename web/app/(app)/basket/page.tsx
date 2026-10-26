"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect, useRef, useState } from "react";
import { useBaskets } from "@/components/BasketsContext";
import { UnauthorizedError } from "@/lib/auth";
import { createBasket, listBaskets, type Basket } from "@/lib/baskets";
import { cents, errorText, pct } from "@/lib/format";
import { selectPicks, type Pick, type Selection } from "@/lib/theses";

const EXAMPLES = [
  "the Fed cuts rates twice before the end of 2027",
  "LeBron never retires",
  "AI regulation passes in the US this year",
  "Bitcoin hits a new all-time high in 2027",
];

const sidePrice = (p: Pick) => (p.side === "yes" ? p.market.yes_price : 1 - p.market.yes_price);

// Basket names are unique per user, so a repeated thesis gets " (2)", " (3)", ...
async function unusedName(thesis: string): Promise<string> {
  const base = thesis.length > 190 ? `${thesis.slice(0, 189)}…` : thesis;
  const taken = new Set((await listBaskets()).map((b) => b.name));
  let name = base;
  for (let n = 2; taken.has(name); n++) name = `${base} (${n})`;
  return name;
}

type Props = { searchParams: Promise<{ theme?: string | string[] }> };

// /build sends its thesis here as ?theme=..., which fills the form and starts the search.
export default function Page({ searchParams }: Props) {
  const { theme } = use(searchParams);
  const initial = (Array.isArray(theme) ? theme[0] : theme)?.trim() ?? "";
  // Remounts the composer when "new basket" is pressed in the sidebar. That remount can land
  // before the URL drops ?theme=, so only the first draft takes the theme.
  const { draftKey } = useBaskets();
  return <Composer key={draftKey} initial={draftKey === 0 ? initial : ""} />;
}

function Composer({ initial }: { initial: string }) {
  const router = useRouter();
  const [thesis, setThesis] = useState(initial);
  const [result, setResult] = useState<Selection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(text = thesis.trim()) {
    if (text.length < 3 || busy) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await selectPicks(text));
    } catch (err) {
      if (err instanceof UnauthorizedError) return router.replace("/login");
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  // Ref, not state: dev-mode Strict Mode runs effects twice and this must only fire once.
  const started = useRef(false);
  useEffect(() => {
    if (started.current || initial.length < 3) return;
    started.current = true;
    submit(initial);
  }, []);

  return (
    <div className="pane">
      <header className="pane-head">
        <div className="eyebrow">new basket</div>
        <h1 className="app-title">what&rsquo;s your take?</h1>
        <p className="pane-sub">
          Describe a view in plain words. We&rsquo;ll find the markets it touches and weight them into one basket.
        </p>
      </header>

      <form
        className="thesis-form"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label htmlFor="thesis" className="sr-only">
          your thesis
        </label>
        <textarea
          id="thesis"
          value={thesis}
          onChange={(e) => setThesis(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
          }}
          placeholder="e.g. the Fed cuts rates twice before the end of 2027"
          rows={3}
          maxLength={2000}
          autoFocus
        />
        <div className="thesis-actions">
          <button type="submit" className="btn" disabled={busy || thesis.trim().length < 3}>
            {busy ? "finding picks…" : "find picks"}
          </button>
          <span className="mono thesis-hint">⌘↵ to run</span>
        </div>
      </form>

      {!result && !busy && (
        <div className="examples">
          <div className="eyebrow">try one</div>
          <div className="examples-list">
            {EXAMPLES.map((ex) => (
              <button key={ex} type="button" onClick={() => setThesis(ex)}>
                {ex}
              </button>
            ))}
          </div>
        </div>
      )}

      {busy && <p className="thesis-note">Reading the markets. This can take up to a minute.</p>}
      {error && (
        <p className="thesis-note auth-error" role="alert">
          {error}
        </p>
      )}

      {result && !busy && <Picks key={result.thesis + result.summary} result={result} />}
    </div>
  );
}

function Picks({ result }: { result: Selection }) {
  const router = useRouter();
  const { refresh } = useBaskets();
  const [saved, setSaved] = useState<Basket | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      setSaved(await createBasket(await unusedName(result.thesis), result.summary, result.positions));
      refresh();
    } catch (err) {
      if (err instanceof UnauthorizedError) return router.replace("/login");
      setError(errorText(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="picks">
      <p className="picks-summary">{result.summary}</p>
      <div className="picks-stats mono">
        <span>{result.picks.length} picks</span>
        <span>{result.candidates_considered} markets considered</span>
        <span>{pct(result.volatility, 1)} daily vol</span>
        <span>{result.diversification_ratio.toFixed(2)}× diversification</span>
      </div>

      {result.picks.length === 0 ? (
        <p className="thesis-note">No markets matched this thesis.</p>
      ) : (
        <>
          <div className="picks-save">
            {saved ? (
              <Link href={`/baskets/${saved.id}`} className="picks-saved">
                saved · open basket <span aria-hidden="true">→</span>
              </Link>
            ) : (
              <button type="button" className="btn" onClick={save} disabled={saving}>
                {saving ? "saving…" : "save basket"}
              </button>
            )}
            {error && (
              <span className="auth-error" role="alert">
                {error}
              </span>
            )}
          </div>
          <ol className="pick-list">
            {result.picks.map((p) => (
              <li key={`${p.market.id}-${p.side}`}>
                <div className="pick-head">
                  <span className="pick-q">
                    {p.market.url ? (
                      <a href={p.market.url} target="_blank" rel="noreferrer">
                        {p.market.question}
                      </a>
                    ) : (
                      p.market.question
                    )}
                  </span>
                  <span className="mono pick-w">{pct(p.weight)}</span>
                </div>
                <div className="pick-meta mono">
                  <span className={`side ${p.side}`}>{p.side}</span>
                  <span>{cents(sidePrice(p))}</span>
                  <span>{p.market.external_id}</span>
                </div>
                <p className="pick-why">{p.rationale}</p>
                <div className="fl-track">
                  <i style={{ width: pct(p.weight) }} />
                </div>
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}
