"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect, useRef, useState } from "react";
import { useBaskets } from "@/components/BasketsContext";
import { Logo } from "@/components/Logo";
import { UnauthorizedError } from "@/lib/auth";
import { useChat } from "@/components/ChatContext";
import { createBasket, type Basket } from "@/lib/baskets";
import { PositionLegend, PositionTable } from "@/components/PositionTable";
import { errorText, pct } from "@/lib/format";
import type { Selection } from "@/lib/theses";

type Props = { searchParams: Promise<{ theme?: string | string[] }> };

const STARTERS = [
  "The Fed cuts rates twice before the end of 2027",
  "The US passes no federal AI safety law this Congress",
  "Bitcoin breaks $150k and crypto gets a market-structure bill",
  "A US recession starts in 2027",
];

// Shown one after another while the API works; it doesn't report real progress.
const STEPS = [
  "Reading your thesis",
  "Scanning open markets",
  "Scoring relevance",
  "Weighting the basket",
];
const STEP_MS = 9000;

// The chat lives in ChatProvider, so leaving this page and coming back keeps it.
// A ?theme=... param asks it as a new thesis, then drops off the URL so it isn't asked again.
export default function Page({ searchParams }: Props) {
  const { theme } = use(searchParams);
  const initial = (Array.isArray(theme) ? theme[0] : theme)?.trim() ?? "";
  const router = useRouter();
  const { turns, busy, ask } = useChat();
  const endRef = useRef<HTMLDivElement>(null);

  // Ref, not state: dev-mode Strict Mode runs effects twice and this must only fire once.
  const started = useRef(false);
  useEffect(() => {
    if (started.current || initial.length < 3) return;
    started.current = true;
    ask(initial);
    router.replace("/basket");
  }, []);

  // Keep the newest message in view as turns are added and answered.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length, busy]);

  if (turns.length === 0) {
    return (
      <div className="chat chat-empty">
        <div className="chat-hello">
          <h1>Thesis</h1>
        </div>
        <Composer onSend={ask} busy={busy} examples={STARTERS} autoFocus />
      </div>
    );
  }

  return (
    <div className="chat">
      <div className="chat-log" aria-live="polite">
        {turns.map((t) => (
          <div key={t.id} className="chat-turn">
            <div className="msg msg-user">
              <p>{t.thesis}</p>
            </div>
            <div className="msg msg-bot">
              <span className="msg-avatar">
                <Logo />
              </span>
              <div className="msg-body">
                {t.result ? (
                  <Picks turnId={t.id} result={t.result} saved={t.saved} />
                ) : t.error ? (
                  <div className="msg-error" role="alert">
                    <p>{t.error}</p>
                    <button
                      type="button"
                      className="chat-chip"
                      onClick={() => ask(t.thesis)}
                      disabled={busy}
                    >
                      try again
                    </button>
                  </div>
                ) : (
                  <Thinking since={t.id} />
                )}
              </div>
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>
      <div className="chat-dock">
        <Composer
          onSend={ask}
          busy={busy}
          placeholder="Refine it, or try another thesis…"
        />
      </div>
    </div>
  );
}

// Types each example into the placeholder, holds it, erases it, then moves to the next.
function useTypedPlaceholder(examples: string[] | undefined, fallback: string) {
  const [shown, setShown] = useState(examples ? "" : fallback);

  useEffect(() => {
    if (!examples?.length) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setShown(examples[0]);
      return;
    }
    let i = 0;
    let len = 0;
    let erasing = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      const full = examples[i];
      if (!erasing) {
        len++;
        if (len === full.length) erasing = true;
      } else {
        len--;
        if (len === 0) {
          erasing = false;
          i = (i + 1) % examples.length;
        }
      }
      setShown(full.slice(0, len));
      timer = setTimeout(tick, !erasing ? 45 : len === full.length ? 2000 : len === 0 ? 500 : 18);
    };
    timer = setTimeout(tick, 400);
    return () => clearTimeout(timer);
  }, [examples, fallback]);

  return shown;
}

function Composer({
  onSend,
  busy,
  autoFocus,
  examples,
  placeholder = "e.g. the Fed cuts rates twice before the end of 2027",
}: {
  onSend: (text: string) => void;
  busy: boolean;
  autoFocus?: boolean;
  // Typed into the placeholder in turn; replaces `placeholder` when given.
  examples?: string[];
  placeholder?: string;
}) {
  // Kept in the chat context so a half-typed thesis survives leaving the page.
  const { draft: text, setDraft: setText } = useChat();
  const ref = useRef<HTMLTextAreaElement>(null);
  const shownPlaceholder = useTypedPlaceholder(examples, placeholder);
  const ready = text.trim().length >= 3 && !busy;

  // Grow with the text up to the CSS max-height, then scroll.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [text]);

  function send() {
    if (!ready) return;
    onSend(text.trim());
    setText("");
  }

  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        send();
      }}
    >
      <label htmlFor="thesis" className="sr-only">
        your thesis
      </label>
      <textarea
        ref={ref}
        id="thesis"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            send();
          }
        }}
        placeholder={shownPlaceholder}
        aria-label={examples ? `your thesis, e.g. ${examples[0]}` : undefined}
        rows={1}
        maxLength={2000}
        autoFocus={autoFocus}
      />
      <div className="composer-bar">
        <span className="composer-hint mono">
          {busy && "finding picks…"}
        </span>
        <button
          type="submit"
          className="composer-send"
          disabled={!ready}
          aria-label="find picks"
        >
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" />
          </svg>
        </button>
      </div>
    </form>
  );
}

// Steps come from time since the request started, so leaving and coming back doesn't restart them.
function Thinking({ since }: { since: number }) {
  const stepAt = () => Math.min(Math.floor((Date.now() - since) / STEP_MS), STEPS.length - 1);
  const [step, setStep] = useState(stepAt);
  useEffect(() => {
    const t = setInterval(() => setStep(stepAt), 1000);
    return () => clearInterval(t);
  }, [since]);

  return (
    <div className="thinking">
      <span className="thinking-dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      <ol>
        {STEPS.map((s, i) => (
          <li key={s} className={i < step ? "done" : i === step ? "now" : ""}>
            {s}
          </li>
        ))}
      </ol>
      <p className="thinking-note">This can take up to a minute.</p>
    </div>
  );
}

function Picks({ turnId, result, saved }: { turnId: number; result: Selection; saved?: Basket }) {
  const router = useRouter();
  const { addUntitled } = useBaskets();
  const { markSaved } = useChat();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      // Saves instantly as "New basket"; the sidebar types in a generated title a moment later.
      const basket = await createBasket(null, result.summary, result.positions);
      markSaved(turnId, basket);
      addUntitled(basket);
    } catch (err) {
      if (err instanceof UnauthorizedError) return router.replace("/login");
      setError(errorText(err));
    } finally {
      setSaving(false);
    }
  }

  if (result.picks.length === 0) {
    return (
      <>
        <p className="msg-text">{result.summary}</p>
        <p className="msg-text msg-muted">
          No open markets matched this thesis. Try making it more concrete, or
          name an event with a date.
        </p>
      </>
    );
  }

  return (
    <>
      <p className="msg-text">{result.summary}</p>
      {result.sub_themes.length > 0 && (
        <ul className="themes">
          {result.sub_themes.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      )}

      <dl className="stats">
        <div>
          <dt>markets</dt>
          <dd>{result.picks.length}</dd>
        </div>
        <div>
          <dt>considered</dt>
          <dd>{result.candidates_considered}</dd>
        </div>
        <div>
          <dt>daily vol</dt>
          <dd>{pct(result.volatility, 1)}</dd>
        </div>
        <div>
          <dt>diversification</dt>
          <dd>{result.diversification_ratio.toFixed(2)}×</dd>
        </div>
      </dl>

      <div className="alloc" aria-hidden="true">
        {result.picks.map((p) => (
          <i
            key={`${p.market.id}-${p.side}`}
            className={p.side}
            style={{ flexGrow: p.weight }}
            title={`${p.market.question} · ${p.side} · ${pct(p.weight)}`}
          />
        ))}
      </div>

      <PositionLegend />
      <PositionTable
        rows={result.picks.map((p) => ({
          market: p.market,
          side: p.side,
          price: p.side === "yes" ? p.market.yes_price : 1 - p.market.yes_price,
          weight: p.weight,
          rationale: p.rationale,
        }))}
      />

      <div className="msg-actions">
        {saved ? (
          <Link href={`/baskets/${saved.id}`} className="btn btn-link">
            open basket to invest <span aria-hidden="true">→</span>
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
        <span className="picks-hint">
          {saved
            ? "Saved to your baskets."
            : "Saving is free. You choose how much to invest next."}
        </span>
        {error && (
          <span className="auth-error" role="alert">
            {error}
          </span>
        )}
      </div>
    </>
  );
}
