export function SetupNotice() {
  return (
    <div className="bs-card max-w-2xl">
      <h1 className="text-xl font-semibold text-[var(--brand-deep)]">Connect Supabase</h1>
      <p className="mt-3 text-sm leading-6 text-[var(--muted)]">
        Set <code className="rounded bg-[#eef3f0] px-1">NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
        <code className="rounded bg-[#eef3f0] px-1">NEXT_PUBLIC_SUPABASE_ANON_KEY</code> in{" "}
        <code className="rounded bg-[#eef3f0] px-1">BusinessSense/.env</code> (repo root; same
        Supabase project as purchase-entry), then restart{" "}
        <code className="rounded bg-[#eef3f0] px-1">npm run dev</code>.
      </p>
    </div>
  );
}
