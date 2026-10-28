"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getMe, logOut, type User } from "@/lib/auth";

// Top-right of every top bar: log in / register, or the signed-in email and log out.
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

  if (user === undefined) return <div className="auth">{launchLink}</div>;

  if (user) {
    return (
      <div className="auth">
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
      <Link href="/login">log in</Link>
      <Link href="/register" className={launch ? undefined : "auth-cta"}>
        register
      </Link>
      {launchLink}
    </div>
  );
}
