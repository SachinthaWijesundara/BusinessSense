/**
 * Friendly "nothing here yet" panel: an icon, a short title, one sentence of guidance and an
 * optional action, so an empty list tells the owner what to do next.
 */

import type { ReactNode } from "react";
import { IconInbox } from "./Icons";

type Props = {
  title: string;
  children?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  className?: string;
};

export function EmptyState({ title, children, icon, actions, className = "" }: Props) {
  return (
    <div className={`bs-empty ${className}`}>
      <span className="bs-empty-icon">{icon ?? <IconInbox />}</span>
      <p className="bs-empty-title">{title}</p>
      {children ? <p className="bs-empty-text">{children}</p> : null}
      {actions ? <div className="bs-empty-actions">{actions}</div> : null}
    </div>
  );
}
