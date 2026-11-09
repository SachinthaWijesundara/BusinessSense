"use client";

/**
 * Sidebar navigation for the five screens in the proposal, around the current screen.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/invoice-manager", label: "Invoice Manager" },
  { href: "/supplier-profile", label: "Supplier Profile" },
  { href: "/purchase-analytics", label: "Purchase Analytics" },
  { href: "/cash-planner", label: "Purchasing Cash Planner" },
] as const;

/** Page frame: the sidebar with the active screen highlighted, and the screen itself. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="bs-shell">
      <aside className="bs-nav">
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
      </aside>
      <main className="bs-main">{children}</main>
    </div>
  );
}
