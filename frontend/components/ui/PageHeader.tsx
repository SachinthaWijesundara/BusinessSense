/**
 * Standard screen heading: title, one line of guidance and a slot for the main action buttons.
 */

import type { ReactNode } from "react";

type Props = { title: string; description?: ReactNode; actions?: ReactNode };

export function PageHeader({ title, description, actions }: Props) {
  return (
    <div className="bs-page-head">
      <div>
        <h1 className="bs-page-title">{title}</h1>
        {description ? <p className="bs-page-sub">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}
