"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect, useRef, useState } from "react";
import { useBaskets } from "@/components/BasketsContext";
import { UnauthorizedError } from "@/lib/auth";
import { createBasket, listBaskets, type Basket } from "@/lib/baskets";
import { PositionLegend, PositionTable } from "@/components/PositionTable";
import { errorText } from "@/lib/format";
import { selectPicks, type Selection } from "@/lib/theses";

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
        <h1 className="app-title">Thesis</h1>
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
          <button
            type="submit"
            className="btn"
            disabled={busy || thesis.trim().length < 3}
          >
            {busy ? "finding picks…" : "find picks"}
          </button>
        </div>
      </form>

      {busy && (
        <p className="thesis-note">
          Reading the markets. This can take up to a minute.
        </p>
      )}
      {error && (
        <p className="thesis-note auth-error" role="alert">
          {error}
        </p>
      )}

      {result && !busy && (
        <Picks key={result.thesis + result.summary} result={result} />
      )}
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
      setSaved(
        await createBasket(
          await unusedName(result.thesis),
          result.summary,
          result.positions,
        ),
      );
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
      {result.picks.length > 0 && (
        <>
          <p className="picks-stats mono">
            {result.picks.length} markets picked from {result.candidates_considered} considered
          </p>
          <PositionLegend />
        </>
      )}

      {result.picks.length === 0 ? (
        <p className="thesis-note">No markets matched this thesis.</p>
      ) : (
        <>
          <div className="picks-save">
            {saved ? (
              <Link href={`/baskets/${saved.id}`} className="picks-saved">
                saved · open it to invest <span aria-hidden="true">→</span>
              </Link>
            ) : (
              <button
                type="button"
                className="btn"
                onClick={save}
                disabled={saving}
              >
                {saving ? "saving…" : "save basket"}
              </button>
            )}
            {!saved && (
              <span className="picks-hint">Saving is free. You choose how much to invest next.</span>
            )}
            {error && (
              <span className="auth-error" role="alert">
                {error}
              </span>
            )}
          </div>
          <PositionTable
            rows={result.picks.map((p) => ({
              market: p.market,
              side: p.side,
              price: p.side === "yes" ? p.market.yes_price : 1 - p.market.yes_price,
              weight: p.weight,
              rationale: p.rationale,
            }))}
          />
        </>
      )}
    </section>
  );
}
