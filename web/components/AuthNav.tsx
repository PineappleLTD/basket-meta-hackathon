"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getMe, logOut, type User } from "@/lib/auth";

// Top-right links: log in / register, or the app links and log out.
export function AuthNav() {
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
        <Link href="/build">new basket</Link>
        <Link href="/baskets">my baskets</Link>
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
