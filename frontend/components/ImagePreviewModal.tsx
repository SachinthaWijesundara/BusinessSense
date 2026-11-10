"use client";

/**
 * Full-viewport lightbox for invoice photos and payment receipt images.
 */

import { useEffect } from "react";

type Props = {
  url: string | null;
  title: string;
  loading?: boolean;
  error?: string;
  onClose: () => void;
};

/** Dark scrim + centered image; Escape / backdrop / Close dismisses. */
export function ImagePreviewModal({ url, title, loading = false, error = "", onClose }: Props) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="image-preview-scrim" role="dialog" aria-modal="true" aria-label={title} onClick={onClose}>
      <div className="image-preview-panel" onClick={(event) => event.stopPropagation()}>
        <div className="image-preview-header">
          <p className="image-preview-title">{title}</p>
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="image-preview-body">
          {loading ? <p className="text-sm text-[var(--muted)]">Loading image…</p> : null}
          {error ? <p className="banner-error">{error}</p> : null}
          {!loading && !error && url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={url} alt={title} className="image-preview-img" />
          ) : null}
          {!loading && !error && !url ? <p className="text-sm text-[var(--muted)]">No image available.</p> : null}
        </div>
      </div>
    </div>
  );
}
