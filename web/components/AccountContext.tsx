"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { UnauthorizedError } from "@/lib/auth";
import { getAccount, type Account } from "@/lib/accounts";
import { errorText } from "@/lib/format";
import { usePoll } from "@/lib/poll";

interface AccountState {
  // null until the first load finishes.
  account: Account | null;
  error: string | null;
  refresh: () => Promise<void>;
  // Takes an account a trade or reset just returned, so every view updates without a re-read.
  setAccount: (account: Account) => void;
}

const AccountContext = createContext<AccountState | null>(null);

// The signed-in user's paper account, shared by the top bar and the pages below it.
export function AccountProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [account, setAccount] = useState<Account | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setAccount(await getAccount());
      setError(null);
    } catch (err) {
      if (err instanceof UnauthorizedError) return router.replace("/login");
      // Keeps what's shown; the next poll tries again.
      setError(errorText(err));
    }
  }, [router]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Follow live prices.
  usePoll(refresh);

  return <AccountContext.Provider value={{ account, error, refresh, setAccount }}>{children}</AccountContext.Provider>;
}

export function useAccount() {
  const state = useContext(AccountContext);
  if (!state) throw new Error("useAccount must be used inside AccountProvider");
  return state;
}
