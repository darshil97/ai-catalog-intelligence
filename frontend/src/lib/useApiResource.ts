"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";

type Result<T> = { path: string; data: T | null; error: ApiError | null };

/**
 * Loads one API resource as the signed-in user.
 *
 * @returns data (null until loaded), error (the last failure, if any), and reload, which
 *   fetches it again after a change. Earlier data stays visible while reloading.
 */
export function useApiResource<T>(path: string) {
  const [version, setVersion] = useState(0);
  const [result, setResult] = useState<Result<T> | null>(null);

  useEffect(() => {
    let cancelled = false;
    api<T>(path)
      .then((data) => {
        if (!cancelled) setResult({ path, data, error: null });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const apiError =
          error instanceof ApiError
            ? error
            : new ApiError(
                0,
                "network",
                "The API isn't available right now. Try again in a minute.",
              );
        setResult((previous) => ({
          path,
          data: previous?.path === path ? previous.data : null,
          error: apiError,
        }));
      });
    return () => {
      cancelled = true;
    };
  }, [path, version]);

  const reload = useCallback(() => setVersion((current) => current + 1), []);
  // Ignore a result for a different path, such as the previous supplier's batches.
  const current = result?.path === path ? result : null;
  return { data: current?.data ?? null, error: current?.error ?? null, reload };
}
