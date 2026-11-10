"use client";

/**
 * Sidebar navigation for the five screens in the proposal, around the current screen.
 * Every screen needs a login: without one the login page opens (and comes back afterwards). The
 * sidebar shows the shop and email that are logged in, with a Log out button.
 */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useSyncExternalStore } from "react";
import { logout, parseSession, readSessionText, subscribeSession } from "@/lib/api";

const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/invoice-manager", label: "Invoice Manager" },
  { href: "/supplier-profile", label: "Supplier Profile" },
  { href: "/purchase-analytics", label: "Purchase Analytics" },
  { href: "/cash-planner", label: "Purchasing Cash Planner" },
] as const;

// What the server renders before the browser can read the stored session.
const NOT_READ_YET = "not-read-yet";

/** Page frame: the sidebar with the active screen highlighted, and the screen itself. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const stored = useSyncExternalStore(subscribeSession, readSessionText, () => NOT_READ_YET);
  const known = stored !== NOT_READ_YET;
  const session = known ? parseSession(stored) : null;
  const onLoginPage = pathname === "/login";

  // Logged out (or the session was refused): open the login page, then come back here.
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
      <aside className="bs-nav flex flex-col">
        <div className="mb-6 px-2">
          <p className="text-lg font-semibold tracking-wide text-white">BusinessSense</p>
          <p className="mt-1 text-xs text-[#a8c4b6]">Owner app · purchasing intelligence</p>
        </div>
        <nav className="flex flex-col gap-1">
          {NAV.map((item) => {
            const active =
              item.href === "/"
                ? pathname === "/"
                : pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <Link key={item.href} href={item.href} className={active ? "active" : undefined}>
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="mt-auto border-t border-[rgb(255_255_255/15%)] px-2 pt-4">
          <p className="text-sm font-semibold text-white">{session.business_name}</p>
          <p className="mt-0.5 break-all text-xs text-[#a8c4b6]">{session.email}</p>
          <button
            type="button"
            className="mt-3 w-full rounded border border-[rgb(255_255_255/25%)] px-3 py-1.5 text-sm text-[#e8f2ec] hover:bg-[rgb(255_255_255/8%)]"
            onClick={() => void logout()}
          >
            Log out
          </button>
        </div>
      </aside>
      <main className="bs-main">{children}</main>
    </div>
  );
}
