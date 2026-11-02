"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { UnauthorizedError } from "@/lib/auth";
import { listBaskets, titleBasket, type Basket } from "@/lib/baskets";
import { errorText } from "@/lib/format";
import { usePoll } from "@/lib/poll";

interface BasketsState {
  // null until the first load finishes.
  baskets: Basket[] | null;
  error: string | null;
  refresh: () => Promise<void>;
  // Shows a just-saved basket under its placeholder name, then swaps in its generated title.
  addUntitled: (basket: Basket) => void;
  // "pending" while a basket's title is being generated, "typing" once it arrives (the UI types it out).
  titles: Record<number, TitleState>;
  // Opens the thesis chat.
  newDraft: () => void;
}

const TYPING_CLEAR_MS = 3000;

export type TitleState = "pending" | "typing";

const BasketsContext = createContext<BasketsState | null>(null);

// The signed-in user's baskets, shared by the sidebar and the pages beside it.
export function BasketsProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [baskets, setBaskets] = useState<Basket[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [titles, setTitles] = useState<Record<number, TitleState>>({});

  const refresh = useCallback(async () => {
    try {
      setBaskets(await listBaskets());
      setError(null);
    } catch (err) {
      if (err instanceof UnauthorizedError) return router.replace("/login");
      setError(errorText(err));
    }
  }, [router]);

  const addUntitled = useCallback(async (basket: Basket) => {
    const setTitle = (state: TitleState | null) =>
      setTitles(({ [basket.id]: _, ...rest }) => (state ? { ...rest, [basket.id]: state } : rest));
    setBaskets((prev) => [basket, ...(prev ?? []).filter((b) => b.id !== basket.id)]);
    setTitle("pending");
    try {
      const { name } = await titleBasket(basket.id);
      setBaskets((prev) => prev?.map((b) => (b.id === basket.id ? { ...b, name } : b)) ?? prev);
      setTitle("typing");
      // Long enough to finish typing; after that, pages opened later show the title as-is.
      setTimeout(() => setTitle(null), TYPING_CLEAR_MS);
    } catch {
      // Keeps the placeholder name; nothing else depends on the title.
      setTitle(null);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Keeps the sidebar's values in step with live prices.
  usePoll(refresh);

  const newDraft = useCallback(() => {
    router.push("/basket");
  }, [router]);

  return (
    <BasketsContext.Provider value={{ baskets, error, refresh, addUntitled, titles, newDraft }}>{children}</BasketsContext.Provider>
  );
}

export function useBaskets() {
  const state = useContext(BasketsContext);
  if (!state) throw new Error("useBaskets must be used inside BasketsProvider");
  return state;
}
