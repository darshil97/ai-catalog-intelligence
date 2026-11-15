"use client";

import { useState, type FormEvent } from "react";

import { ConfirmButton } from "@/components/ConfirmButton";
import { ApiError, api, jsonBody } from "@/lib/api";
import type {
  Approval,
  GeneratedContent,
  History,
  Saved,
  ValidationIssue,
} from "@/lib/types";
import { useApiResource } from "@/lib/useApiResource";

/** Turns an API failure into words, saying so plainly when the product changed elsewhere. */
function failure(caught: unknown, fallback: string): string {
  if (caught instanceof ApiError) return caught.message;
  return fallback;
}

export const APPROVAL_TEXT: Record<Approval["state"], string> = {
  draft: "Draft",
  approved: "Approved",
  changed_since_approval: "Changed since approval",
  rejected: "Rejected",
};

export const APPROVAL_BADGE: Record<Approval["state"], string> = {
  draft: "",
  approved: "ok",
  changed_since_approval: "warning",
  rejected: "error",
};

/** The product's approval: its state, and Approve, Reject or Restore (D40). */
export function ApprovalBar({
  productId,
  approval,
  onChanged,
}: {
  productId: string;
  approval: Approval;
  onChanged: () => void;
}) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function send(path: string, body: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      await api<Saved>(
        path,
        jsonBody("POST", { ...body, revision: approval.revision }),
      );
      setRejecting(false);
      setReason("");
    } catch (caught) {
      setError(failure(caught, "Couldn't save that."));
    } finally {
      setBusy(false);
      onChanged();
    }
  }

  const base = `/products/${productId}`;
  const settled =
    approval.state === "approved" || approval.state === "rejected";
  return (
    <div className="approval-bar">
      <div className="row">
        <span className={`badge ${APPROVAL_BADGE[approval.state]}`}>
          {APPROVAL_TEXT[approval.state]}
        </span>
        {approval.state !== "approved" && approval.state !== "rejected" && (
          <button
            type="button"
            className="button primary"
            disabled={busy || !approval.can_approve}
            onClick={() => send(`${base}/approve`, {})}
          >
            {approval.state === "changed_since_approval"
              ? "Approve again"
              : "Approve"}
          </button>
        )}
        {approval.state !== "rejected" && !rejecting && (
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={() => setRejecting(true)}
          >
            Reject
          </button>
        )}
        {settled && (
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={() => send(`${base}/restore`, {})}
          >
            Back to draft
          </button>
        )}
      </div>
      {approval.state === "rejected" && approval.rejection_reason && (
        <p className="muted">Rejected: {approval.rejection_reason}</p>
      )}
      {!approval.can_approve &&
        approval.blockers.length > 0 &&
        approval.state !== "rejected" && (
          <ul className="reasons">
            {approval.blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
        )}
      {rejecting && (
        <form
          className="confirm"
          onSubmit={(event: FormEvent) => {
            event.preventDefault();
            void send(`${base}/reject`, { reason });
          }}
        >
          <input
            className="input"
            value={reason}
            maxLength={500}
            required
            placeholder="Why it's rejected"
            aria-label="Reason for rejecting"
            onChange={(event) => setReason(event.target.value)}
          />
          <button
            type="submit"
            className="button danger"
            disabled={busy || !reason.trim()}
          >
            Reject
          </button>
          <button
            type="button"
            className="button link"
            onClick={() => setRejecting(false)}
          >
            Cancel
          </button>
        </form>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

/** Set or clear one value, as a person's decision that processing never overwrites. */
export function ValueEditor({
  productId,
  code,
  name,
  current,
  revision,
  onDone,
}: {
  productId: string;
  code: string;
  name: string;
  current: string | null;
  revision: number;
  onDone: (saved: boolean) => void;
}) {
  const [value, setValue] = useState(current ?? "");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save(clear: boolean) {
    setBusy(true);
    setError(null);
    try {
      await api<Saved>(
        `/products/${productId}/values/${code}`,
        jsonBody("PUT", {
          value: clear ? null : value,
          note: note || null,
          revision,
        }),
      );
      onDone(true);
    } catch (caught) {
      setError(failure(caught, "Couldn't save that."));
      setBusy(false);
    }
  }

  return (
    <form
      className="value-editor"
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        void save(false);
      }}
    >
      <label>
        <span className="muted">New {name.toLowerCase()}</span>
        <input
          className="input"
          value={value}
          maxLength={500}
          autoFocus
          placeholder="Such as 1.5 L, 640 g, Vardhan"
          onChange={(event) => setValue(event.target.value)}
        />
      </label>
      <label>
        <span className="muted">Note for the history (optional)</span>
        <input
          className="input"
          value={note}
          maxLength={500}
          placeholder="Such as: checked with the supplier"
          onChange={(event) => setNote(event.target.value)}
        />
      </label>
      <div className="row">
        <button
          type="submit"
          className="button primary small"
          disabled={busy || !value.trim()}
        >
          Save
        </button>
        {current && (
          <button
            type="button"
            className="button small"
            disabled={busy}
            onClick={() => save(true)}
          >
            Clear it
          </button>
        )}
        <button
          type="button"
          className="button link"
          onClick={() => onDone(false)}
        >
          Cancel
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

/** Write or change the copy by hand. Automatic writing never replaces it. */
export function CopyEditor({
  productId,
  content,
  revision,
  onDone,
}: {
  productId: string;
  content: GeneratedContent;
  revision: number;
  onDone: (saved: boolean) => void;
}) {
  const [title, setTitle] = useState(content.title ?? "");
  const [description, setDescription] = useState(content.description ?? "");
  const [bullets, setBullets] = useState((content.bullets ?? []).join("\n"));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api<Saved>(
        `/products/${productId}/copy`,
        jsonBody("PUT", {
          title,
          description,
          bullets: bullets.split("\n").filter((line) => line.trim()),
          revision,
        }),
      );
      onDone(true);
    } catch (caught) {
      setError(failure(caught, "Couldn't save the copy."));
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={save}>
      <label className="stack">
        <span className="muted">Title</span>
        <input
          className="input wide"
          value={title}
          required
          onChange={(event) => setTitle(event.target.value)}
        />
      </label>
      <label className="stack">
        <span className="muted">Description</span>
        <textarea
          className="input wide"
          rows={4}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </label>
      <label className="stack">
        <span className="muted">Bullets, one per line</span>
        <textarea
          className="input wide"
          rows={5}
          value={bullets}
          onChange={(event) => setBullets(event.target.value)}
        />
      </label>
      <p className="muted">
        Your copy is kept as written. Anything it says that the product&apos;s
        values don&apos;t shows up as a warning in its checks.
      </p>
      <div className="row">
        <button
          type="submit"
          className="button primary"
          disabled={busy || !title.trim()}
        >
          Save my copy
        </button>
        <button
          type="button"
          className="button link"
          onClick={() => onDone(false)}
        >
          Cancel
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

/** Settle a flagged duplicate pair from one of its checks. */
export function DuplicateActions({
  productId,
  issue,
  revision,
  onChanged,
}: {
  productId: string;
  issue: ValidationIssue;
  revision: number;
  onChanged: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  if (!issue.other_product_id || !issue.rule.startsWith("duplicate_"))
    return null;

  async function settle(keep: "this" | "other" | "different") {
    setError(null);
    try {
      await api<Saved>(
        `/products/${productId}/duplicate`,
        jsonBody("POST", { other_id: issue.other_product_id, keep, revision }),
      );
    } catch (caught) {
      setError(failure(caught, "Couldn't save that."));
    } finally {
      onChanged();
    }
  }

  return (
    <span className="row">
      <ConfirmButton
        className="button small"
        label="Same product: keep this"
        question="Reject the other one as a duplicate?"
        confirmLabel="Keep this"
        onConfirm={() => settle("this")}
      />
      <ConfirmButton
        className="button small"
        label="Keep the other"
        question="Reject this one as a duplicate?"
        confirmLabel="Keep the other"
        onConfirm={() => settle("other")}
      />
      {issue.rule !== "duplicate_gtin" && (
        <button
          type="button"
          className="button small"
          onClick={() => settle("different")}
        >
          Different products
        </button>
      )}
      {error && (
        <span className="error" role="alert">
          {error}
        </span>
      )}
    </span>
  );
}

/** Every recorded change to the product, newest first. */
export function HistorySection({
  productId,
  version,
}: {
  productId: string;
  version: number;
}) {
  const history = useApiResource<History>(
    `/products/${productId}/history?v=${version}`,
  );
  const items = history.data?.items ?? [];
  return (
    <section className="section">
      <h2>History</h2>
      {history.error && <p className="error">{history.error.message}</p>}
      {items.length === 0 && !history.error && (
        <p className="muted">Loading…</p>
      )}
      <ol className="timeline">
        {items.map((entry, index) => (
          <li key={`${entry.at}-${index}`}>
            <span
              className={`dot${entry.by === "system" ? " system" : ""}`}
              aria-hidden="true"
            />
            <span>
              <strong>
                {entry.by === "you"
                  ? "You"
                  : entry.by === "system"
                    ? "System"
                    : entry.by}
              </strong>{" "}
              {entry.text.charAt(0).toLowerCase() + entry.text.slice(1)}
              {typeof entry.after?.note === "string" && entry.after.note && (
                <span className="muted"> · “{entry.after.note}”</span>
              )}
              <br />
              <span className="muted">
                {new Date(entry.at).toLocaleString()}
              </span>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
