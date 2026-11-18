/**
 * Skeleton loaders: grey shimmering shapes that hold the place of content while it loads,
 * so screens do not jump or show bare "Loading…" text.
 */

type SkeletonProps = { className?: string; style?: React.CSSProperties };

/** One shimmering block; size it with width/height classes or style. */
export function Skeleton({ className = "", style }: SkeletonProps) {
  return <span className={`bs-skeleton ${className}`} style={style} aria-hidden="true" />;
}

/** Several text-like lines of slightly different widths. */
export function SkeletonLines({ lines = 3 }: { lines?: number }) {
  const widths = ["100%", "88%", "72%", "94%", "60%"];
  return (
    <div className="space-y-2">
      {Array.from({ length: lines }, (_, index) => (
        <Skeleton key={index} className="h-3.5" style={{ width: widths[index % widths.length] }} />
      ))}
    </div>
  );
}

/** A stat-card-shaped placeholder. */
export function SkeletonStat() {
  return (
    <div className="bs-card bs-stat">
      <Skeleton className="h-10 w-10 shrink-0" />
      <div className="w-full space-y-2">
        <Skeleton className="h-3 w-1/2" />
        <Skeleton className="h-6 w-1/3" />
      </div>
    </div>
  );
}

/** A card with a title and a few lines. */
export function SkeletonCard({ lines = 4, height }: { lines?: number; height?: number }) {
  return (
    <div className="bs-card space-y-3" style={height ? { minHeight: height } : undefined}>
      <Skeleton className="h-4 w-1/3" />
      <SkeletonLines lines={lines} />
    </div>
  );
}

/** Table-shaped placeholder: a header bar and rows of cells. */
export function SkeletonTable({ rows = 6, columns = 6 }: { rows?: number; columns?: number }) {
  return (
    <div className="space-y-2 p-3" role="status" aria-live="polite" aria-label="Loading">
      <Skeleton className="h-8 w-full" />
      {Array.from({ length: rows }, (_, row) => (
        <div key={row} className="grid gap-3" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
          {Array.from({ length: columns }, (_, column) => (
            <Skeleton key={column} className="h-4" />
          ))}
        </div>
      ))}
    </div>
  );
}

/** Whole-screen placeholder: title, a row of stat cards and two content cards. */
export function PageSkeleton({ stats = 4 }: { stats?: number }) {
  return (
    <div className="space-y-5" role="status" aria-live="polite" aria-label="Loading">
      <div className="space-y-2">
        <Skeleton className="h-7 w-56" />
        <Skeleton className="h-4 w-80 max-w-full" />
      </div>
      {stats > 0 ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: stats }, (_, index) => (
            <SkeletonStat key={index} />
          ))}
        </div>
      ) : null}
      <SkeletonCard lines={5} height={180} />
      <SkeletonCard lines={3} />
    </div>
  );
}
