"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { CHAT_KEY, UnauthorizedError } from "@/lib/auth";
import type { Basket } from "@/lib/baskets";
import { errorText } from "@/lib/format";
import { selectPicks, type Selection } from "@/lib/theses";

// `id` is also when the request started, which drives the progress steps.
export type Turn = { id: number; thesis: string; result?: Selection; error?: string; saved?: Basket };

type Stored = { turns: Turn[]; draft: string };

// A request can't outlive a reload, so a turn still waiting on one is shown as failed.
function load(): Stored | null {
  try {
    const stored: Stored | null = JSON.parse(localStorage.getItem(CHAT_KEY) ?? "null");
    if (!stored) return null;
    const turns = stored.turns.map((t) =>
      t.result || t.error ? t : { ...t, error: "Interrupted by a page reload." },
    );
    return { turns, draft: stored.draft };
  } catch {
    return null;
  }
}

interface ChatState {
  turns: Turn[];
  busy: boolean;
  // Unsent composer text, kept so it survives leaving the page.
  draft: string;
  setDraft: (text: string) => void;
  ask: (text: string) => void;
  markSaved: (turnId: number, basket: Basket) => void;
}

const ChatContext = createContext<ChatState | null>(null);

// The one thesis chat, held above the pages so it survives navigating to a basket and back.
// Requests keep running while the page is closed and land here when they finish.
// Saved to localStorage so a reload keeps it too; auth clears it when the token changes.
export function ChatProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");
  // Ref alongside the state so two quick sends can't both get through before a re-render.
  const busyRef = useRef(false);
  // Ref so Strict Mode's second effect run doesn't restore twice; state so saving waits
  // until the restored turns are in, rather than writing over them with the empty start.
  const restored = useRef(false);
  const [loaded, setLoaded] = useState(false);

  // Read after mount so the server render matches. Child effects (a ?theme= ask) run first,
  // so anything they added goes after the restored turns instead of being overwritten.
  useEffect(() => {
    if (restored.current) return;
    restored.current = true;
    const stored = load();
    if (stored) {
      setTurns((ts) => [...stored.turns, ...ts]);
      setDraft((d) => d || stored.draft);
    }
    setLoaded(true);
  }, []);

  useEffect(() => {
    if (!loaded) return;
    try {
      localStorage.setItem(CHAT_KEY, JSON.stringify({ turns, draft } satisfies Stored));
    } catch {}
  }, [loaded, turns, draft]);

  const patch = useCallback((id: number, p: Partial<Turn>) => {
    setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, ...p } : t)));
  }, []);

  const ask = useCallback(
    async (text: string) => {
      if (text.length < 3 || busyRef.current) return;
      busyRef.current = true;
      setBusy(true);
      const id = Date.now();
      setTurns((ts) => [...ts, { id, thesis: text }]);
      try {
        patch(id, { result: await selectPicks(text) });
      } catch (err) {
        if (err instanceof UnauthorizedError) return router.replace("/login");
        patch(id, { error: errorText(err) });
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    },
    [patch, router],
  );

  const markSaved = useCallback((turnId: number, basket: Basket) => patch(turnId, { saved: basket }), [patch]);

  return (
    <ChatContext.Provider value={{ turns, busy, draft, setDraft, ask, markSaved }}>{children}</ChatContext.Provider>
  );
}

export function useChat() {
  const state = useContext(ChatContext);
  if (!state) throw new Error("useChat must be used inside ChatProvider");
  return state;
}
