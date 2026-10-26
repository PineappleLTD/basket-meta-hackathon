"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { UnauthorizedError } from "@/lib/auth";
import { listBaskets, type Basket } from "@/lib/baskets";
import { errorText } from "@/lib/format";

interface BasketsState {
  // null until the first load finishes.
  baskets: Basket[] | null;
  error: string | null;
  refresh: () => Promise<void>;
  // Bumped by "new basket" so the composer resets even when it's already open.
  draftKey: number;
  newDraft: () => void;
}

const BasketsContext = createContext<BasketsState | null>(null);

// The signed-in user's baskets, shared by the sidebar and the pages beside it.
export function BasketsProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [baskets, setBaskets] = useState<Basket[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draftKey, setDraftKey] = useState(0);

  const refresh = useCallback(async () => {
    try {
      setBaskets(await listBaskets());
      setError(null);
    } catch (err) {
      if (err instanceof UnauthorizedError) return router.replace("/login");
      setError(errorText(err));
    }
  }, [router]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const newDraft = useCallback(() => {
    setDraftKey((k) => k + 1);
    router.push("/basket");
  }, [router]);

  return (
    <BasketsContext.Provider value={{ baskets, error, refresh, draftKey, newDraft }}>{children}</BasketsContext.Provider>
  );
}

export function useBaskets() {
  const state = useContext(BasketsContext);
  if (!state) throw new Error("useBaskets must be used inside BasketsProvider");
  return state;
}
