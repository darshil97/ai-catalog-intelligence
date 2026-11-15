"use client";

import { useState } from "react";

import { ApiError, api } from "@/lib/api";
import { describeLocation, VALUE_FLAG_TEXT } from "@/lib/location";
import type {
  CategoryNode,
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
  const categories = useApiResource<{ items: CategoryNode[] }>("/categories");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const items = tasks.data?.items ?? [];
  const open = items.filter((task) => task.status === "open");
  const pending = tasks.data?.pending_photo_values ?? 0;

  if (items.length === 0 && pending === 0) return null;

  async function send(task: ReviewTask, body: Record<string, unknown>) {
    setError(null);
    setMessage(null);
    try {
      const result = await api<DecisionResult>(
        `/review-tasks/${task.id}/decision`,
        { method: "POST", body: JSON.stringify(body) },
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

  async function decide(task: ReviewTask, productId: string | null) {
    await send(
      task,
      productId
        ? { decision: "accept", product_id: productId }
        : { decision: "reject" },
    );
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
          ? `${open.length} ${open.length === 1 ? "decision is" : "decisions are"} waiting for you: which product a photo shows, which value is right when sources disagree, or a product's category. Nothing here is decided automatically.`
          : "Everything has been decided."}
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
        {open.map((task) =>
          task.kind === "image_match" ? (
            <ReviewItem key={task.id} task={task} onDecide={decide} />
          ) : task.kind === "attribute_conflict" ? (
            <ConflictItem key={task.id} task={task} onSend={send} />
          ) : (
            <CategoryItem
              key={task.id}
              task={task}
              categories={categories.data?.items ?? []}
              onSend={send}
            />
          ),
        )}
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
        <img src={task.thumbnail_url} alt={task.image_name ?? "Photo"} />
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

type Send = (task: ReviewTask, body: Record<string, unknown>) => Promise<void>;

function ConflictItem({ task, onSend }: { task: ReviewTask; onSend: Send }) {
  const [chosen, setChosen] = useState(task.values[0]?.id ?? "own");
  const [own, setOwn] = useState("");
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      await onSend(
        task,
        chosen === "own"
          ? { decision: "set", value: own }
          : { decision: "choose", value_id: chosen },
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="review-item single">
      <div className="stack">
        <strong>
          {task.product_key}: {task.attribute_name}
        </strong>
        <span className="muted">The sources disagree. Which is right?</span>
        <fieldset className="candidates">
          <legend className="visually-hidden">
            {task.attribute_name} for {task.product_key}
          </legend>
          {task.values.map((value) => (
            <label key={value.id} className="candidate">
              <input
                type="radio"
                name={`task-${task.id}`}
                checked={chosen === value.id}
                onChange={() => setChosen(value.id)}
              />
              <span>
                <strong>{value.display}</strong>{" "}
                <span className="muted">
                  &ldquo;{value.raw_value}&rdquo; in {value.source_file_name}
                  {describeLocation(value.location)
                    ? `, ${describeLocation(value.location)}`
                    : ""}
                  {value.flags.length > 0 &&
                    ` (${value.flags.map((f) => VALUE_FLAG_TEXT[f] ?? f).join(", ")})`}
                </span>
              </span>
            </label>
          ))}
          <label className="candidate">
            <input
              type="radio"
              name={`task-${task.id}`}
              checked={chosen === "own"}
              onChange={() => setChosen("own")}
            />
            <span className="stack">
              <span>Something else:</span>
              <input
                className="input"
                value={own}
                onChange={(event) => {
                  setOwn(event.target.value);
                  setChosen("own");
                }}
                placeholder="Type the right value"
              />
            </span>
          </label>
        </fieldset>
        <span className="actions">
          <button
            type="button"
            className="button primary"
            disabled={busy || (chosen === "own" && !own.trim())}
            onClick={save}
          >
            Save
          </button>
        </span>
      </div>
    </li>
  );
}

function CategoryItem({
  task,
  categories,
  onSend,
}: {
  task: ReviewTask;
  categories: CategoryNode[];
  onSend: Send;
}) {
  const [chosen, setChosen] = useState(task.suggested_category_id ?? "");
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      await onSend(task, { decision: "choose", category_id: chosen });
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="review-item single">
      <div className="stack">
        <strong>{task.product_key}: category</strong>
        <span className="muted">
          {task.suggested_category_name
            ? `Suggested: ${task.suggested_category_name}, but not with enough certainty.`
            : "No category was suggested."}
          {task.category_reason ? ` ${task.category_reason}` : ""}
        </span>
        <label className="stack">
          <span className="visually-hidden">Category</span>
          <select
            className="input"
            value={chosen}
            onChange={(event) => setChosen(event.target.value)}
          >
            <option value="" disabled>
              Choose a category
            </option>
            {categories.map((category) => (
              <option key={category.id} value={category.id}>
                {category.path}
              </option>
            ))}
          </select>
        </label>
        <span className="actions">
          <button
            type="button"
            className="button primary"
            disabled={busy || !chosen}
            onClick={save}
          >
            Save
          </button>
        </span>
      </div>
    </li>
  );
}
