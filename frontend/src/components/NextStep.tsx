"use client";

import { useState } from "react";

import { ApiError, api } from "@/lib/api";
import type {
  BatchApproval,
  BatchApprovalResult,
  BatchEnrichment,
  BatchValidation,
  EnrichmentStarted,
} from "@/lib/types";

export type NextTab = "review" | "copy" | "checks";

type Step = {
  title: string;
  text: string;
  reasons?: string[];
  action?: { label: string; run: () => void | Promise<void> };
  done?: boolean;
};

/**
 * The one thing to do next on a processed batch, highlighted: decide open review items, then
 * write copy, then fix the checks' errors. When nothing is left, it says the batch is ready.
 */
export function NextStep({
  batchId,
  openReviews,
  enrichment,
  validation,
  approval,
  onTab,
  onChanged,
}: {
  batchId: string;
  openReviews: number | null;
  enrichment: BatchEnrichment | null;
  validation: BatchValidation | null;
  approval: BatchApproval | null;
  onTab: (tab: NextTab) => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (openReviews === null || !enrichment || !validation) return null;
  if (enrichment.items.length === 0) return null;

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      await api<EnrichmentStarted>(`/batches/${batchId}/enrichment`, {
        method: "POST",
      });
      onTab("copy");
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Couldn't start writing.",
      );
    } finally {
      setBusy(false);
      onChanged();
    }
  }

  async function approveReady() {
    setBusy(true);
    setError(null);
    try {
      await api<BatchApprovalResult>(`/batches/${batchId}/approve`, {
        method: "POST",
        body: JSON.stringify({}),
      });
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Couldn't approve.",
      );
    } finally {
      setBusy(false);
      onChanged();
    }
  }

  const writing =
    enrichment.run !== null &&
    (enrichment.run.status === "queued" || enrichment.run.status === "running");
  const withErrors = validation.items.filter((item) => item.errors > 0).length;
  const total = validation.items.length;
  const plural = (n: number, one: string, many: string) =>
    `${n} ${n === 1 ? one : many}`;

  let step: Step;
  if (openReviews > 0) {
    step = {
      title: `${plural(openReviews, "decision is", "decisions are")} waiting for you`,
      text: "Confirm photos, choose between values that disagree, and pick categories. Each one shows its evidence side by side. Copy is written automatically once you've decided them, so it includes the values you settle.",
      action: { label: "Start reviewing", run: () => onTab("review") },
    };
  } else if (writing) {
    step = {
      title: "Writing titles and bullets…",
      text: "Copy is being written from the settled values. This takes a few seconds per product.",
      action: { label: "Watch progress", run: () => onTab("copy") },
    };
  } else if (enrichment.run?.status === "paused") {
    step = {
      title: "Copy writing is paused",
      text: "This month's AI budget was used up partway. Nothing was lost; resume it once there's budget again.",
      action: { label: "Open copy", run: () => onTab("copy") },
    };
  } else if (enrichment.can_generate > 0 && !enrichment.budget_reached) {
    step = {
      title: `${plural(enrichment.can_generate, "product needs", "products need")} copy`,
      text: "Copy is written from settled values, and these changed or are new since it was written:",
      reasons: enrichment.items
        .filter((item) => item.can_generate)
        .flatMap((item) =>
          item.reasons.map((reason) => `${item.product_key}: ${reason}`),
        ),
      action: {
        label: busy ? "Starting…" : "Generate copy",
        run: generate,
      },
    };
  } else if (withErrors > 0) {
    step = {
      title: `${plural(withErrors, "product has", "products have")} errors to fix for ${validation.profile_name}`,
      text: "Errors must be fixed before a product can be approved. Warnings can be set aside with a reason.",
      action: { label: "See checks", run: () => onTab("checks") },
    };
  } else if (approval && approval.ready > 0) {
    step = {
      title: `${plural(approval.ready, "product is", "products are")} ready to approve`,
      text: "Approve them to include them in exports. A product that changes after approval needs approving again.",
      action: {
        label: busy ? "Approving…" : "Approve all ready",
        run: approveReady,
      },
    };
  } else if (approval && approval.approved > 0) {
    step = {
      title: `${plural(approval.approved, "product is", "products are")} approved`,
      text: `${approval.approved} of ${total} approved for ${validation.profile_name}. Exporting arrives in the next update.`,
      done: true,
    };
  } else {
    step = {
      title: `All ${plural(total, "product is", "products are")} ready for ${validation.profile_name}`,
      text: "Nothing is waiting for you.",
      done: true,
    };
  }

  return (
    <section
      className={`next-step${step.done ? " done" : ""}`}
      aria-live="polite"
    >
      <div>
        <p className="eyebrow">{step.done ? "All done" : "Next step"}</p>
        <h2>{step.title}</h2>
        <p className="muted">{step.text}</p>
        {step.reasons && step.reasons.length > 0 && (
          <ul className="reasons">
            {step.reasons.slice(0, 4).map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
            {step.reasons.length > 4 && (
              <li>and {step.reasons.length - 4} more on the Copy tab</li>
            )}
          </ul>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </div>
      {step.action && (
        <button
          type="button"
          className="button primary large"
          disabled={busy}
          onClick={() => void step.action?.run()}
        >
          {step.action.label} →
        </button>
      )}
    </section>
  );
}
