"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getMe, logOut, type User } from "@/lib/auth";

// Top-right links: log in / register, or a link into the app and log out.
// Inside the app the sidebar does the navigating, so `inApp` shows the email instead.
export function AuthNav({ inApp = false }: { inApp?: boolean }) {
  // undefined until we've checked, so signed-in users don't see a flash of the logged-out links.
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const router = useRouter();

  useEffect(() => {
    getMe().then(setUser);
  }, []);

  if (user === undefined) return <div className="auth" />;

  if (user) {
    return (
      <div className="auth">
        {inApp ? <span className="auth-email">{user.email}</span> : <Link href="/basket">my baskets</Link>}
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
      </div>
    );
  }

  return (
    <div className="auth">
      <Link href="/login">log in</Link>
      <Link href="/register" className="auth-cta">
        register
      </Link>
    </div>
  );
}
