"use client";

import { useCallback, useEffect, useState } from "react";
import { db, friendlyError } from "./supabase";
import type { Item } from "./types";

export function useItems() {
  const [items, setItems] = useState<Item[]>([]);
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    const { data, error: queryError } = await db()
      .from("items")
      .select("id, sku, name")
      .order("name");
    if (queryError) {
      setError(friendlyError(queryError));
      return;
    }
    setItems((data ?? []) as Item[]);
  }, []);

  useEffect(() => {
    reload().catch((err) => setError(friendlyError(err)));
  }, [reload]);

  const remember = useCallback(
    async (name: string, sku: string) => {
      const trimmed = name.trim();
      if (!trimmed) return;
      const existing = items.find(
        (item) => item.name.toLowerCase() === trimmed.toLowerCase(),
      );
      if (existing) {
        if (sku.trim() && !existing.sku) {
          await db().from("items").update({ sku: sku.trim() }).eq("id", existing.id);
          await reload();
        }
        return;
      }
      const { error: insertError } = await db()
        .from("items")
        .insert({ name: trimmed, sku: sku.trim() || null });
      if (insertError && !insertError.message.includes("duplicate")) {
        setError(friendlyError(insertError));
        return;
      }
      await reload();
    },
    [items, reload],
  );

  return { items, error, reload, remember };
}
