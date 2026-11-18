/**
 * Headline number with an icon. `tone` colours the icon so the owner can see at a glance which
 * figures ask for action (warn / bad) and which are just information. Pass `href` to make the
 * whole card a link.
 */

import Link from "next/link";
import type { ReactNode } from "react";

type Props = {
  label: string;
  value: string;
  icon: ReactNode;
  tone?: "default" | "warn" | "bad" | "info";
  note?: string;
  cta?: string;
  href?: string;
};

export function StatCard({ label, value, icon, tone = "default", note, cta, href }: Props) {
  const body = (
    <div className={`bs-card bs-stat tone-${tone}`}>
      <span className="bs-stat-icon">{icon}</span>
      <div className="min-w-0">
        <p className="bs-stat-label">{label}</p>
        <p className="bs-stat-value tabular">{value}</p>
        {note ? <p className="bs-stat-note">{note}</p> : null}
        {href && cta ? <p className="bs-stat-cta">{cta} →</p> : null}
      </div>
    </div>
  );
  return href ? (
    <Link href={href} className="block h-full">
      {body}
    </Link>
  ) : (
    body
  );
}
