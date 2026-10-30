"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { DbStatus } from "@/components/DbStatus";
import { logIn, register } from "@/lib/auth";
import { Logo } from "@/components/Logo";

type Mode = "login" | "register";

const COPY = {
  login: {
    title: "log in",
    submit: "log in",
    busy: "logging in…",
    alt: "no account?",
    altLink: "register",
    altHref: "/register",
  },
  register: {
    title: "make an account",
    submit: "register",
    busy: "creating account…",
    alt: "already have one?",
    altLink: "log in",
    altHref: "/login",
  },
};

export function AuthForm({ mode }: { mode: Mode }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const copy = COPY[mode];

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const email = String(form.get("email"));
    const password = String(form.get("password"));
    setBusy(true);
    setError(null);
    try {
      await (mode === "login" ? logIn : register)(email, password);
      router.push("/basket");
    } catch (err) {
      setError(
        err instanceof Error && err.message !== "Failed to fetch"
          ? err.message
          : "Couldn't reach the server",
      );
      setBusy(false);
    }
  }

  return (
    <main className="auth-page">
      <div className="brand">
        <Link href="/" className="mark">
          <Logo />
          basket
        </Link>
        <DbStatus />
      </div>
      <form className="auth-form" onSubmit={onSubmit}>
        <h1>{copy.title}</h1>
        <label>
          <span className="eyebrow">email</span>
          <input
            name="email"
            type="email"
            autoComplete="email"
            required
            autoFocus
          />
        </label>
        <label>
          <span className="eyebrow">password</span>
          <input
            name="password"
            type="password"
            autoComplete={
              mode === "login" ? "current-password" : "new-password"
            }
            minLength={8}
            required
          />
          {mode === "register" && (
            <span className="auth-hint">at least 8 characters</span>
          )}
        </label>
        {error && (
          <p className="auth-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy}>
          {busy ? copy.busy : copy.submit}
        </button>
        <p className="auth-alt">
          {copy.alt} <Link href={copy.altHref}>{copy.altLink}</Link>
        </p>
      </form>
    </main>
  );
}
