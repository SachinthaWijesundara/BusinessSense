"use client";

/**
 * Login page: the shop owner's email and password. They are sent to the backend (POST /auth/login),
 * which checks them with Supabase Auth; the browser never talks to Supabase. There is no sign-up:
 * the BusinessSense team creates each owner's login. After logging in, the owner returns to the
 * screen they asked for (?next=/some/screen, only paths inside this app are followed).
 */

import { useRouter, useSearchParams } from "next/navigation";
import Image from "next/image";
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
    <form onSubmit={submit} className="bs-card w-full max-w-sm space-y-4 !p-7" style={{ boxShadow: "var(--shadow-md)" }}>
      <div>
        <p className="flex items-center gap-2 text-xl font-semibold text-[var(--brand-deep)] min-[900px]:hidden">
          <Image src="/businesssense-icon.png" alt="" width={30} height={30} className="rounded-md" />
          BusinessSense
        </p>
        <h1 className="mt-2 text-2xl font-semibold text-[var(--brand-deep)] min-[900px]:mt-0">Welcome back</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">Log in to your shop.</p>
      </div>
      {error ? (
        <p className="banner-error" role="alert">
          {error}
        </p>
      ) : null}
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
        {busy ? (
          <>
            <span className="extract-spinner !h-4 !w-4 !border-2 !border-white/40 !border-t-white" aria-hidden="true" />
            Logging in…
          </>
        ) : (
          "Log in"
        )}
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
    <div className="bs-login">
      <aside className="bs-login-aside">
        <p className="flex items-center gap-3 text-xl font-semibold text-white">
          <Image src="/businesssense-icon.png" alt="" width={40} height={40} className="rounded-md" priority />
          BusinessSense
        </p>
        <div>
          <p className="max-w-md text-3xl font-semibold leading-tight text-white">
            Know what you owe, what you paid and what to set aside.
          </p>
          <ul className="bs-login-points mt-6 max-w-md">
            <li>
              <span>1</span>
              <span>Every invoice stays a draft until you check and verify it.</span>
            </li>
            <li>
              <span>2</span>
              <span>Unusual prices are flagged for you to look at, never changed for you.</span>
            </li>
            <li>
              <span>3</span>
              <span>The cash planner shows its working, so you can see where each figure comes from.</span>
            </li>
          </ul>
        </div>
        <p className="text-sm text-[#86ad9b]">Owner app · purchasing intelligence</p>
      </aside>
      <div className="bs-login-main">
        <Suspense fallback={<div className="bs-skeleton h-72 w-full max-w-sm" />}>
          <LoginForm />
        </Suspense>
      </div>
    </div>
  );
}
