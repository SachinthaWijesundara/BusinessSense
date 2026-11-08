"use client";

import { useMemo, useRef, useState } from "react";
import type { Item } from "@/lib/types";

type Props = {
  kind: "name" | "sku";
  value: string;
  items: Item[];
  placeholder?: string;
  className?: string;
  onChange: (value: string) => void;
  onPick: (item: Item) => void;
};

export function ItemAutocomplete({
  kind,
  value,
  items,
  placeholder,
  className,
  onChange,
  onPick,
}: Props) {
  const [open, setOpen] = useState(false);
  const blurTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const matches = useMemo(() => {
    const q = value.trim().toLowerCase();
    if (!q) return items.slice(0, 8);
    return items
      .filter((item) => {
        const hay = kind === "name" ? item.name : item.sku ?? "";
        return hay.toLowerCase().includes(q);
      })
      .slice(0, 8);
  }, [items, kind, value]);

  return (
    <div className="relative">
      <input
        className={className ?? "cell-input"}
        value={value}
        placeholder={placeholder}
        autoComplete="off"
        onFocus={() => {
          if (blurTimer.current) clearTimeout(blurTimer.current);
          setOpen(true);
        }}
        onBlur={() => {
          blurTimer.current = setTimeout(() => setOpen(false), 150);
        }}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
        }}
      />
      {open && matches.length > 0 && (
        <ul className="absolute z-20 mt-0.5 max-h-48 w-full min-w-[14rem] overflow-auto border border-[var(--line)] bg-white shadow-md">
          {matches.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                className="flex w-full flex-col items-start px-2 py-1.5 text-left text-sm hover:bg-[#f3f6f4]"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  onPick(item);
                  setOpen(false);
                }}
              >
                <span>{kind === "name" ? item.name : item.sku || "—"}</span>
                <span className="text-xs text-[var(--muted)]">
                  {kind === "name" ? item.sku || "no SKU" : item.name}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
