"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ThemeToggle } from "@/components/ThemeToggle";
import { getMe, logOut, type User } from "@/lib/auth";

// Top-right of every top bar: the theme toggle, settings when signed in, then log in / register, or the signed-in email and log out.
// `launch` adds a button into the app (used on the landing page).
export function AuthNav({ launch = false }: { launch?: boolean }) {
  // undefined until we've checked, so signed-in users don't see a flash of the logged-out links.
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const router = useRouter();

  useEffect(() => {
    getMe().then(setUser);
  }, []);

  const launchLink = launch && (
    <Link href="/basket" className="auth-cta">
      launch
    </Link>
  );

  if (user === undefined) return (
      <div className="auth">
        <ThemeToggle />
        {launchLink}
      </div>
    );

  if (user) {
    return (
      <div className="auth">
        <ThemeToggle />
        <Link href="/settings" className="theme-toggle" aria-label="Settings" title="Settings">
          <svg viewBox="0 0 24 24" aria-hidden="true" style={{ strokeWidth: 2 }}>
            <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
            <circle cx="12" cy="12" r="3" />
          </svg>
        </Link>
        <span className="auth-email">{user.email}</span>
        <button
          type="button"
          title={user.email}
          onClick={() =>
            logOut().then(() => {
              setUser(null);
              router.push("/");
            })
          }
        >
          log out
        </button>
        {launchLink}
      </div>
    );
  }

  return (
    <div className="auth">
      <ThemeToggle />
      <Link href="/login">log in</Link>
      <Link href="/register" className={launch ? undefined : "auth-cta"}>
        register
      </Link>
      {launchLink}
    </div>
  );
}
