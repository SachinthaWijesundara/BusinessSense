"use client";

/**
 * Login page: the shop owner's email and password. They are sent to the backend (POST /auth/login),
 * which checks them with Supabase Auth; the browser never talks to Supabase. There is no sign-up:
 * the BusinessSense team creates each owner's login. After logging in, the owner returns to the
 * screen they asked for (?next=/some/screen, only paths inside this app are followed).
 */

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, useSyncExternalStore, type FormEvent } from "react";
import { errorMessage, login, parseSession, readSessionText, subscribeSession } from "@/lib/api";

/** Only follow a "next" address inside this app (never another site or a script). */
function safeNext(value: string | null): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("\\") || value.startsWith("/login")) {
    return "/";
  }
  return value;
}

/** The email and password form. */
function LoginForm() {
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const loggedIn = parseSession(useSyncExternalStore(subscribeSession, readSessionText, () => null)) !== null;
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Already logged in (or just logged in): open the screen that was asked for.
  useEffect(() => {
    if (loggedIn) router.replace(next);
  }, [loggedIn, next, router]);

  /** Send the email and password to the backend. */
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!email.trim() || !password) {
      setError("Enter your email and password.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await login(email.trim(), password);
    } catch (err) {
      setError(errorMessage(err));
      setPassword("");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="bs-card w-full max-w-sm space-y-4">
      <div>
        <p className="text-2xl font-semibold text-[var(--brand-deep)]">BusinessSense</p>
        <p className="mt-1 text-sm text-[var(--muted)]">Log in to your shop.</p>
      </div>
      {error ? <p className="banner-error">{error}</p> : null}
      <label className="block">
        <span className="mb-1 block text-sm font-medium">Email</span>
        <input
          className="field"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
      </label>
      <label className="block">
        <span className="mb-1 block text-sm font-medium">Password</span>
        <input
          className="field"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      </label>
      <button className="btn btn-primary w-full" type="submit" disabled={busy}>
        {busy ? "Logging in…" : "Log in"}
      </button>
      <p className="text-xs text-[var(--muted)]">
        Logins are created by the BusinessSense team. Forgot your password? Ask the team to reset it.
      </p>
    </form>
  );
}

/** Login page; Suspense is needed because the form reads ?next= from the address. */
export default function LoginPage() {
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <Suspense fallback={<p className="text-[var(--muted)]">Loading…</p>}>
        <LoginForm />
      </Suspense>
    </div>
  );
}
