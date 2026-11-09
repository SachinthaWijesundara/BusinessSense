/**
 * Placeholder for a screen whose backend part is not built yet (Purchase Analytics, Cash Planner).
 */

type Props = {
  title: string;
  blurb: string;
  needs: string;
};

/** Screen title, what the screen will show, and what it is waiting for in the backend. */
export function EmptyScreen({ title, blurb, needs }: Props) {
  return (
    <div>
      <h1 className="text-2xl font-semibold text-[var(--brand-deep)]">{title}</h1>
      <p className="mt-1 max-w-2xl text-sm text-[var(--muted)]">{blurb}</p>
      <div className="bs-card mt-6">
        <div className="bs-empty">
          <p className="text-sm font-medium text-[var(--brand-deep)]">Not available yet</p>
          <p className="mt-2 text-sm">{needs}</p>
        </div>
      </div>
    </div>
  );
}
