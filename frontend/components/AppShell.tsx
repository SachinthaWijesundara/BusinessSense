"use client";

/**
 * Sidebar navigation for the five screens in the proposal, around the current screen.
 * Desktop: collapsed icon rail that expands with labels on hover/focus. Mobile: full labels.
 * Every screen needs a login: without one the login page opens (and comes back afterwards).
 */

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useSyncExternalStore, type ReactNode } from "react";
import { logout, parseSession, readSessionText, subscribeSession } from "@/lib/api";

const NAV = [
  { href: "/", label: "Dashboard", icon: IconDashboard },
  { href: "/invoice-manager", label: "Invoice Manager", icon: IconInvoice },
  { href: "/supplier-profile", label: "Supplier Profile", icon: IconSupplier },
  { href: "/purchase-analytics", label: "Purchase Analytics", icon: IconAnalytics },
  { href: "/cash-planner", label: "Purchasing Cash Planner", icon: IconCash },
] as const;

const NOT_READ_YET = "not-read-yet";

function IconDashboard() {
  return (
    <svg className="bs-nav-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 13h7V4H4v9zm0 7h7v-5H4v5zm9 0h7V11h-7v9zm0-16v5h7V4h-7z" fill="currentColor" />
    </svg>
  );
}

function IconInvoice() {
  return (
    <svg className="bs-nav-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M7 3h10a2 2 0 0 1 2 2v16l-3-1.5L13 21l-3-1.5L7 21V5a2 2 0 0 1 2-2zm2 5h6v2H9V8zm0 4h6v2H9v-2zm0 4h4v2H9v-2z"
        fill="currentColor"
      />
    </svg>
  );
}

function IconSupplier() {
  return (
    <svg className="bs-nav-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4zm0 2c-4 0-8 2-8 4v2h16v-2c0-2-4-4-8-4z"
        fill="currentColor"
      />
    </svg>
  );
}

function IconAnalytics() {
  return (
    <svg className="bs-nav-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 19h16v2H4v-2zm2-2V9h2v8H6zm5 0V5h2v12h-2zm5 0v-6h2v6h-2z" fill="currentColor" />
    </svg>
  );
}

function IconCash() {
  return (
    <svg className="bs-nav-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M3 6h18v12H3V6zm2 2v8h14V8H5zm7 1.5A2.5 2.5 0 1 1 9.5 12 2.5 2.5 0 0 1 12 9.5zM6 10h1.5v4H6v-4zm10.5 0H18v4h-1.5v-4z"
        fill="currentColor"
      />
    </svg>
  );
}

/** Page frame: icon rail + main screen. */
export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const stored = useSyncExternalStore(subscribeSession, readSessionText, () => NOT_READ_YET);
  const known = stored !== NOT_READ_YET;
  const session = known ? parseSession(stored) : null;
  const onLoginPage = pathname === "/login";

  useEffect(() => {
    if (known && !session && !onLoginPage) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [known, session, onLoginPage, pathname, router]);

  if (onLoginPage) return <>{children}</>;
  if (!session) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="text-[var(--muted)]">Checking your login…</p>
      </div>
    );
  }

  return (
    <div className="bs-shell">
      <aside className="bs-nav flex flex-col" tabIndex={-1}>
        <div className="bs-nav-brand mb-6 px-1">
          <p className="bs-nav-brand-mark">
            <Image src="/businesssense-icon.png" alt="BusinessSense" width={36} height={36} className="rounded-md" priority />
          </p>
          <div className="bs-nav-label-block">
            <p className="flex items-center gap-2 text-lg font-semibold tracking-wide text-white">
              <Image src="/businesssense-icon.png" alt="" width={32} height={32} className="rounded-md" />
              BusinessSense
            </p>
            <p className="mt-1 text-xs text-[#a8c4b6]">Owner app · purchasing intelligence</p>
          </div>
        </div>
        <nav className="flex flex-col gap-1">
          {NAV.map((item) => {
            const active =
              item.href === "/"
                ? pathname === "/"
                : pathname === item.href || pathname.startsWith(`${item.href}/`);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={active ? "active bs-nav-link" : "bs-nav-link"}
                title={item.label}
              >
                <Icon />
                <span className="bs-nav-label">{item.label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="bs-nav-footer mt-auto border-t border-[rgb(255_255_255/15%)] px-1 pt-4">
          <div className="bs-nav-label-block">
            <p className="text-sm font-semibold text-white">{session.business_name}</p>
            <p className="mt-0.5 break-all text-xs text-[#a8c4b6]">{session.email}</p>
          </div>
          <button
            type="button"
            className="bs-nav-logout mt-3 w-full rounded border border-[rgb(255_255_255/25%)] px-2 py-1.5 text-sm text-[#e8f2ec] hover:bg-[rgb(255_255_255/8%)]"
            onClick={() => void logout()}
            title="Log out"
          >
            <span className="bs-nav-logout-icon" aria-hidden="true">
              ⎋
            </span>
            <span className="bs-nav-label">Log out</span>
          </button>
        </div>
      </aside>
      <main className="bs-main">{children}</main>
    </div>
  );
}
