"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type {
  BatchEnrichment,
  EnrichmentStarted,
  EnrichmentState,
  ProductEnrichment,
} from "@/lib/types";
import { useApiResource } from "@/lib/useApiResource";

const POLL_MS = 5000;
const ACTIVE = new Set(["queued", "running"]);

export const ENRICHMENT_STATE_TEXT: Record<EnrichmentState, string> = {
  none: "Not written yet",
  generated: "Written",
  out_of_date: "Out of date: its facts changed",
  skipped: "Not enough settled information",
  failed: "Couldn't be written",
};

/**
 * Titles, descriptions and bullets written from settled facts only (D37). Generate covers
 * products whose text is missing, failed or out of date. A run that reaches the monthly LLM
 * budget pauses, and can be resumed later without losing anything.
 */
export function EnrichmentPanel({
  batchId,
  version,
}: {
  batchId: string;
  version: number;
}) {
  const state = useApiResource<BatchEnrichment>(
    `/batches/${batchId}/enrichment`,
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { reload } = state;
  const run = state.data?.run ?? null;
  const active = run ? ACTIVE.has(run.status) : false;

  // Reload when a review decision elsewhere on the page changes facts.
  useEffect(() => {
    if (version > 0) reload();
  }, [version, reload]);

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(reload, POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, reload]);

  if (!state.data) {
    return state.error ? (
      <p className="error" role="alert">
        {state.error.message}
      </p>
    ) : null;
  }
  const data = state.data;
  if (data.items.length === 0) return null;

  async function send(path: string) {
    setBusy(true);
    setError(null);
    try {
      await api<EnrichmentStarted>(path, { method: "POST" });
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Couldn't start writing the text.",
      );
    } finally {
      setBusy(false);
      reload();
    }
  }

  const paused = run?.status === "paused";
  return (
    <section className="stack" aria-live="polite">
      <h2>Generated content</h2>
      <p className="muted">
        Titles, descriptions and bullets written only from settled values. Every
        line names the values it uses, and anything a value doesn&apos;t say is
        refused.
      </p>
      {data.open_reviews > 0 && (
        <p className="notice">
          {data.open_reviews}{" "}
          {data.open_reviews === 1 ? "decision is" : "decisions are"} waiting in
          review. Those values are left out until you decide them.
        </p>
      )}
      {paused && (
        <div className="notice">
          <p>
            Paused: this month&apos;s LLM budget was used up, so {run.blocked}{" "}
            {run.blocked === 1 ? "product is" : "products are"} waiting. Nothing
            was lost.
          </p>
          <button
            type="button"
            className="button"
            disabled={busy || data.budget_reached}
            onClick={() => send(`/batches/${batchId}/enrichment/resume`)}
          >
            Resume
          </button>
          {data.budget_reached && (
            <p className="muted">
              Resume becomes available once the budget is raised or the month
              ends.
            </p>
          )}
        </div>
      )}
      {active && run && (
        <p className="muted">
          {run.status === "queued"
            ? "Waiting for the worker to start…"
            : `Writing… ${run.done} of ${run.total} done.`}
        </p>
      )}
      {!active && !paused && (
        <div className="actions">
          <button
            type="button"
            className="button primary"
            disabled={busy || data.can_generate === 0 || data.budget_reached}
            onClick={() => send(`/batches/${batchId}/enrichment`)}
          >
            {busy
              ? "Starting…"
              : data.can_generate > 0
                ? `Generate for ${data.can_generate} ${data.can_generate === 1 ? "product" : "products"}`
                : "Everything is up to date"}
          </button>
        </div>
      )}
      {data.budget_reached && !paused && (
        <p className="muted">
          This month&apos;s LLM budget has been used, so no text can be written
          for now.
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <ul className="list">
        {data.items.map((item) => (
          <EnrichmentRow key={item.product_id} item={item} />
        ))}
      </ul>
    </section>
  );
}

function EnrichmentRow({ item }: { item: ProductEnrichment }) {
  const warn = item.state === "failed" || item.state === "out_of_date";
  return (
    <li>
      <span>
        <Link href={`/products/${item.product_id}`}>{item.product_key}</Link>{" "}
        <span className={`badge${warn ? " warning" : ""}`}>
          {ENRICHMENT_STATE_TEXT[item.state]}
        </span>
      </span>
      <span className="muted">
        {item.title ?? `${item.facts} settled values`}
        {item.left_out > 0 && ` · ${item.left_out} left out`}
      </span>
      {item.message && <p className="muted file-reason">{item.message}</p>}
    </li>
  );
}
