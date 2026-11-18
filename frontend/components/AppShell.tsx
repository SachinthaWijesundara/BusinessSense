"use client";

/**
 * Page frame around every screen: sidebar, mobile top bar and drawer, breadcrumbs, skip link.
 * Desktop: an icon rail that slides open over the page on hover/focus, with a button to pin it
 * open (remembered in this browser). Mobile: a top bar with a menu button that opens a drawer.
 * Every screen needs a login: without one the login page opens (and comes back afterwards).
 */

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { logout, parseSession, readSessionText, subscribeSession } from "@/lib/api";
import {
  IconAnalytics,
  IconCash,
  IconChevronsRight,
  IconClose,
  IconDashboard,
  IconInvoice,
  IconLogout,
  IconMenu,
  IconSupplier,
} from "@/components/ui/Icons";
import { PageSkeleton } from "@/components/ui/Skeleton";

const NAV = [
  { href: "/", label: "Dashboard", icon: IconDashboard },
  { href: "/invoice-manager", label: "Invoice Manager", icon: IconInvoice },
  { href: "/supplier-profile", label: "Supplier Profile", icon: IconSupplier },
  { href: "/purchase-analytics", label: "Purchase Analytics", icon: IconAnalytics },
  { href: "/cash-planner", label: "Purchasing Cash Planner", icon: IconCash },
] as const;

const NOT_READ_YET = "not-read-yet";
const PIN_KEY = "bs-nav-pinned";
const PIN_EVENT = "bs-nav-pinned-change";

/** Pinned-open preference, kept in this browser only. */
function subscribePinned(onChange: () => void) {
  window.addEventListener(PIN_EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(PIN_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

function readPinned(): boolean {
  try {
    return window.localStorage.getItem(PIN_KEY) === "1";
  } catch {
    return false;
  }
}

function writePinned(value: boolean) {
  try {
    window.localStorage.setItem(PIN_KEY, value ? "1" : "0");
  } catch {
    // Storage blocked: the rail still works, it just forgets the choice.
  }
  window.dispatchEvent(new Event(PIN_EVENT));
}

/** Where the current path sits: the screen it belongs to, plus a trail for nested screens. */
function screenInfo(pathname: string) {
  const section = NAV.find((item) => (item.href === "/" ? pathname === "/" : pathname === item.href || pathname.startsWith(`${item.href}/`)));
  let nested: string | null = null;
  if (pathname === "/invoice-manager/new") nested = "Type in an invoice";
  else if (section?.href === "/invoice-manager" && pathname !== "/invoice-manager") nested = "Review invoice";
  return { section, nested };
}

/** Page frame: sidebar + main screen. */
export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const stored = useSyncExternalStore(subscribeSession, readSessionText, () => NOT_READ_YET);
  const pinned = useSyncExternalStore(subscribePinned, readPinned, () => false);
  // The drawer is open only for the path it was opened on, so moving to another screen closes it.
  const [drawerPath, setDrawerPath] = useState<string | null>(null);
  const drawerOpen = drawerPath === pathname;
  const known = stored !== NOT_READ_YET;
  const session = known ? parseSession(stored) : null;
  const onLoginPage = pathname === "/login";

  useEffect(() => {
    if (known && !session && !onLoginPage) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [known, session, onLoginPage, pathname, router]);

  // Escape closes the mobile drawer.
  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDrawerPath(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  if (onLoginPage) return <>{children}</>;
  if (!session) {
    return (
      <div className="bs-main">
        <div className="bs-content">
          <PageSkeleton stats={0} />
        </div>
      </div>
    );
  }

  const { section, nested } = screenInfo(pathname);
  const shellClass = `bs-shell${pinned ? " is-pinned" : ""}${drawerOpen ? " is-drawer-open" : ""}`;

  return (
    <div className={shellClass}>
      <a href="#main" className="bs-skip">
        Skip to content
      </a>

      <header className="bs-topbar">
        <button type="button" className="bs-icon-btn" onClick={() => setDrawerPath(pathname)} aria-label="Open menu" aria-expanded={drawerOpen}>
          <IconMenu className="h-6 w-6" />
        </button>
        <span className="bs-topbar-title">{section?.label ?? "BusinessSense"}</span>
      </header>

      <aside className="bs-nav" aria-label="Main navigation">
        <div className="bs-nav-panel">
          <div className="bs-nav-brand">
            <Image src="/businesssense-icon.png" alt="" width={34} height={34} priority />
            <span className="bs-nav-label">BusinessSense</span>
            <button
              type="button"
              className="bs-icon-btn bs-drawer-close"
              onClick={() => setDrawerPath(null)}
              aria-label="Close menu"
            >
              <IconClose className="h-5 w-5" />
            </button>
          </div>

          <p className="bs-nav-section">Workspace</p>
          <nav className="flex flex-col">
            {NAV.map((item) => {
              const active = section?.href === item.href;
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={active ? "active bs-nav-link" : "bs-nav-link"}
                  aria-current={active ? "page" : undefined}
                  title={item.label}
                >
                  <Icon className="bs-nav-icon" />
                  <span className="bs-nav-label">{item.label}</span>
                </Link>
              );
            })}
          </nav>

          <div className="bs-nav-footer">
            <div className="bs-nav-account">
              <span className="bs-nav-avatar" aria-hidden="true">
                {(session.business_name ?? "?").trim().charAt(0).toUpperCase() || "?"}
              </span>
              <div className="bs-nav-account-text">
                <p className="truncate text-sm font-semibold">{session.business_name}</p>
                <p className="truncate text-xs text-[var(--muted)]">{session.email}</p>
              </div>
            </div>
            <button type="button" className="bs-nav-btn bs-nav-pin" onClick={() => writePinned(!pinned)} aria-pressed={pinned} title={pinned ? "Collapse menu" : "Keep menu open"}>
              <IconChevronsRight className="bs-nav-icon" />
              <span className="bs-nav-label">{pinned ? "Collapse menu" : "Keep menu open"}</span>
            </button>
            <button type="button" className="bs-nav-btn" onClick={() => void logout()} title="Log out">
              <IconLogout className="bs-nav-icon" />
              <span className="bs-nav-label">Log out</span>
            </button>
          </div>
        </div>
      </aside>

      <div className="bs-scrim" onClick={() => setDrawerPath(null)} aria-hidden="true" />

      <main id="main" className="bs-main" tabIndex={-1}>
        <div className="bs-content" key={pathname}>
          {section && nested ? (
            <nav className="bs-crumbs" aria-label="Breadcrumb">
              <Link href="/">Home</Link>
              <span aria-hidden="true">/</span>
              <Link href={section.href}>{section.label}</Link>
              <span aria-hidden="true">/</span>
              <span aria-current="page">{nested}</span>
            </nav>
          ) : null}
          {children}
        </div>
      </main>
    </div>
  );
}
