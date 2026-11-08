type Props = {
  title: string;
  blurb: string;
};

export function EmptyScreen({ title, blurb }: Props) {
  return (
    <div>
      <h1 className="text-2xl font-semibold text-[var(--brand-deep)]">{title}</h1>
      <p className="mt-1 max-w-2xl text-sm text-[var(--muted)]">{blurb}</p>
      <div className="bs-card mt-6">
        <div className="bs-empty">
          <p className="text-sm font-medium text-[var(--brand-deep)]">Coming soon</p>
          <p className="mt-2 text-sm">
            This screen is empty for now. We will build it next, one screen at a time.
          </p>
        </div>
      </div>
    </div>
  );
}
