"use client";

import { useState } from "react";

import { ApiError, api } from "@/lib/api";
import type {
  DecisionResult,
  PhotoValuesStatus,
  ReviewTask,
  ReviewTaskList,
} from "@/lib/types";
import { useApiResource } from "@/lib/useApiResource";

const PHOTO_VALUES_TEXT: Record<PhotoValuesStatus, string> = {
  queued: "Reading this photo's details for the product now.",
  waiting:
    "Another batch is processing, so this photo's details will be read later. Use Read details below when it finishes.",
  not_needed: "",
};

/**
 * Photos whose product a person needs to confirm, with the evidence for each candidate.
 * A decision is recorded with who made it; accepting a photo reads its details for the product.
 */
export function ReviewPanel({
  batchId,
  onDecided,
}: {
  batchId: string;
  onDecided: () => void;
}) {
  const tasks = useApiResource<ReviewTaskList>(
    `/batches/${batchId}/review-tasks`,
  );
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const items = tasks.data?.items ?? [];
  const open = items.filter((task) => task.status === "open");
  const pending = tasks.data?.pending_photo_values ?? 0;

  if (items.length === 0 && pending === 0) return null;

  async function decide(task: ReviewTask, productId: string | null) {
    setError(null);
    setMessage(null);
    try {
      const result = await api<DecisionResult>(
        `/review-tasks/${task.id}/decision`,
        {
          method: "POST",
          body: JSON.stringify(
            productId
              ? { decision: "accept", product_id: productId }
              : { decision: "reject" },
          ),
        },
      );
      setMessage(PHOTO_VALUES_TEXT[result.photo_values] || null);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Couldn't save that.",
      );
    } finally {
      tasks.reload();
      onDecided();
    }
  }

  async function readValues() {
    setError(null);
    try {
      const result = await api<{ photo_values: PhotoValuesStatus }>(
        `/batches/${batchId}/photo-values`,
        { method: "POST" },
      );
      setMessage(PHOTO_VALUES_TEXT[result.photo_values] || null);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Couldn't start reading details.",
      );
    } finally {
      tasks.reload();
    }
  }

  return (
    <section className="stack">
      <h2>Needs review</h2>
      <p className="muted">
        {open.length
          ? `${open.length} ${open.length === 1 ? "photo needs" : "photos need"} you to confirm which product ${open.length === 1 ? "it shows" : "they show"}. A photo is only linked automatically when its own code is found on it.`
          : "Every photo has been decided."}
      </p>
      {message && <p className="notice">{message}</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {pending > 0 && (
        <p>
          {pending} accepted {pending === 1 ? "photo hasn't" : "photos haven't"}{" "}
          been read for details yet.{" "}
          <button type="button" className="button" onClick={readValues}>
            Read details
          </button>
        </p>
      )}
      <ul className="review-list">
        {open.map((task) => (
          <ReviewItem key={task.id} task={task} onDecide={decide} />
        ))}
      </ul>
    </section>
  );
}

function ReviewItem({
  task,
  onDecide,
}: {
  task: ReviewTask;
  onDecide: (task: ReviewTask, productId: string | null) => void;
}) {
  const [chosen, setChosen] = useState(task.candidates[0]?.product_id ?? "");
  const [busy, setBusy] = useState(false);

  async function act(productId: string | null) {
    setBusy(true);
    try {
      await onDecide(task, productId);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="review-item">
      {task.thumbnail_url ? (
        // Signed links to private storage: next/image can't optimise them.
        // eslint-disable-next-line @next/next/no-img-element
        <img src={task.thumbnail_url} alt={task.image_name} />
      ) : (
        <span className="thumb-missing">No preview</span>
      )}
      <div className="stack">
        <strong>{task.image_name}</strong>
        <fieldset className="candidates">
          <legend className="visually-hidden">
            Which product does {task.image_name} show?
          </legend>
          {task.candidates.map((candidate) => (
            <label key={candidate.id} className="candidate">
              <input
                type="radio"
                name={`task-${task.id}`}
                value={candidate.product_id}
                checked={chosen === candidate.product_id}
                onChange={() => setChosen(candidate.product_id)}
              />
              <span>
                <strong>{candidate.product_key}</strong>{" "}
                <span className="muted">
                  ({Math.round(candidate.score * 100)}% evidence)
                </span>
                <ul className="evidence">
                  {candidate.evidence.map((item) => (
                    <li key={item.signal}>{item.text}</li>
                  ))}
                </ul>
              </span>
            </label>
          ))}
        </fieldset>
        <span className="actions">
          <button
            type="button"
            className="button primary"
            disabled={busy || !chosen}
            onClick={() => act(chosen)}
          >
            Accept
          </button>
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={() => act(null)}
          >
            None of these
          </button>
        </span>
      </div>
    </li>
  );
}
