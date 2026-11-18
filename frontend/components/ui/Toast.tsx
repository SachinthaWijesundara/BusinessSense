"use client";

/**
 * Toast notifications: short messages that appear in the corner and fade away on their own
 * (for example "Payment confirmed"). Wrap the app in <ToastProvider> once, then call
 * `const toast = useToast(); toast.ok("Saved")` from any component.
 * Errors stay on screen longer and can always be closed by hand.
 */

import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

type Tone = "ok" | "error" | "info";
type ToastItem = { id: number; tone: Tone; text: string };

type ToastApi = {
  ok: (text: string) => void;
  error: (text: string) => void;
  info: (text: string) => void;
};

const ToastContext = createContext<ToastApi | null>(null);

const LIFETIME_MS: Record<Tone, number> = { ok: 4000, info: 5000, error: 8000 };

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setItems((current) => current.filter((item) => item.id !== id));
  }, []);

  const push = useCallback(
    (tone: Tone, text: string) => {
      const id = nextId.current++;
      setItems((current) => [...current.slice(-3), { id, tone, text }]);
      window.setTimeout(() => dismiss(id), LIFETIME_MS[tone]);
    },
    [dismiss],
  );

  const api = useMemo<ToastApi>(
    () => ({
      ok: (text) => push("ok", text),
      error: (text) => push("error", text),
      info: (text) => push("info", text),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="bs-toasts" aria-live="polite" aria-atomic="false">
        {items.map((item) => (
          <div key={item.id} className={`bs-toast tone-${item.tone}`} role={item.tone === "error" ? "alert" : "status"}>
            <span className="text-sm">{item.text}</span>
            <button type="button" className="bs-toast-close" onClick={() => dismiss(item.id)} aria-label="Dismiss">
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/** The toast functions. Safe to call outside a provider (it then does nothing). */
export function useToast(): ToastApi {
  const context = useContext(ToastContext);
  return context ?? { ok: () => {}, error: () => {}, info: () => {} };
}
