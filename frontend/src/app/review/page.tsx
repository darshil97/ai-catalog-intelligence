"use client";

import Link from "next/link";

import { RequireActive } from "@/components/Account";
import { ReviewPanel } from "@/components/ReviewPanel";
import type { ReviewSummary } from "@/lib/types";
import { useApiResource } from "@/lib/useApiResource";

export default function ReviewPage() {
  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Review</h1>
          <p className="sub">
            Everything waiting for your decision, across all your batches:
            photos to confirm, values that disagree, and categories to choose.
          </p>
        </div>
      </div>
      <RequireActive>
        <ReviewQueue />
      </RequireActive>
    </main>
  );
}

function ReviewQueue() {
  const summary = useApiResource<ReviewSummary>("/review");
  if (!summary.data) {
    return summary.error ? (
      <p className="error" role="alert">
        {summary.error.message}
      </p>
    ) : (
      <p className="muted">Loading…</p>
    );
  }
  if (summary.data.batches.length === 0) {
    return (
      <p className="empty">
        Nothing is waiting for you. New items appear here when a batch is
        processed.
      </p>
    );
  }
  return (
    <div className="stack">
      {summary.data.batches.map((batch) => (
        <div key={batch.batch_id} className="stack">
          <div className="card-head">
            <h2>
              <Link href={`/batches/${batch.batch_id}`}>
                {batch.batch_name}
              </Link>{" "}
              <span className="muted" style={{ fontSize: "0.95rem" }}>
                · {batch.supplier_name}
              </span>
            </h2>
            <span className="badge warning">{batch.open} open</span>
          </div>
          <ReviewPanel batchId={batch.batch_id} onDecided={summary.reload} />
        </div>
      ))}
    </div>
  );
}
