"use client";

/**
 * Product picker: a text box that suggests catalogue products by name or code as the owner types.
 * Used on the review screen to link an invoice line to a product by hand.
 */

import { useMemo, useRef, useState } from "react";
import type { Product } from "@/lib/types";

type Props = {
  products: Product[];
  placeholder?: string;
  onPick: (product: Product) => void;
};

/** The search box and its drop-down list of matching products. */
export function ItemAutocomplete({ products, placeholder = "Search products…", onPick }: Props) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const blurTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const matches = useMemo(() => {
    const text = query.trim().toLowerCase();
    const found = text
      ? products.filter(
          (product) =>
            product.name.toLowerCase().includes(text) || (product.sku ?? "").toLowerCase().includes(text),
        )
      : products;
    return found.slice(0, 8);
  }, [products, query]);

  return (
    <div className="relative">
      <input
        className="field"
        value={query}
        placeholder={placeholder}
        autoComplete="off"
        onFocus={() => {
          if (blurTimer.current) clearTimeout(blurTimer.current);
          setOpen(true);
        }}
        onBlur={() => {
          blurTimer.current = setTimeout(() => setOpen(false), 150);
        }}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
        }}
      />
      {open && matches.length > 0 ? (
        <ul className="absolute z-20 mt-0.5 max-h-48 w-full min-w-[14rem] overflow-auto border border-[var(--line)] bg-white shadow-md">
          {matches.map((product) => (
            <li key={product.id}>
              <button
                type="button"
                className="flex w-full flex-col items-start px-2 py-1.5 text-left text-sm hover:bg-[#f3f6f4]"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => {
                  onPick(product);
                  setQuery("");
                  setOpen(false);
                }}
              >
                <span>{product.name}</span>
                <span className="text-xs text-[var(--muted)]">{product.sku || "no code"}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
