"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { APPROVAL_BADGE, APPROVAL_TEXT } from "@/components/ProductReview";
import { ApiError, api, jsonBody } from "@/lib/api";
import type { BatchApproval, BatchApprovalResult } from "@/lib/types";

/**
 * Each product's approval in a batch, with "approve all ready" and "reject selected" (D40).
 * Approving one at a time happens on the product page.
 */
export function ApprovalPanel({
  batchId,
  approval,
  onChanged,
}: {
  batchId: string;
  approval: BatchApproval | null;
  onChanged: () => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!approval || approval.items.length === 0) return null;

  async function approveReady() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await api<BatchApprovalResult>(
        `/batches/${batchId}/approve`,
        jsonBody("POST", {}),
      );
      const skipped = result.skipped.length;
      setMessage(
        `Approved ${result.approved.length}.` +
          (skipped ? ` ${skipped} not ready yet; each says why below.` : ""),
      );
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Couldn't approve.",
      );
    } finally {
      setBusy(false);
      onChanged();
    }
  }

  async function rejectSelected(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api<BatchApprovalResult>(
        `/batches/${batchId}/reject`,
        jsonBody("POST", { product_ids: [...selected], reason }),
      );
      setSelected(new Set());
      setRejecting(false);
      setReason("");
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Couldn't reject.",
      );
    } finally {
      setBusy(false);
      onChanged();
    }
  }

  function toggle(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <section className="section">
      <div className="card-head">
        <h2>Approval</h2>
        <span className="muted">
          {approval.approved} of {approval.items.length} approved
        </span>
      </div>
      <p className="muted">
        A product can be approved once it has no errors, its copy is current and
        nothing about it waits in review. Only approved products are exported,
        and a product that changes after approval needs approving again.
      </p>
      <div className="row">
        <button
          type="button"
          className="button primary"
          disabled={busy || approval.ready === 0}
          onClick={approveReady}
        >
          Approve all ready ({approval.ready})
        </button>
        {!rejecting && (
          <button
            type="button"
            className="button"
            disabled={busy || selected.size === 0}
            onClick={() => setRejecting(true)}
          >
            Reject selected ({selected.size})
          </button>
        )}
      </div>
      {rejecting && (
        <form className="confirm" onSubmit={rejectSelected}>
          <input
            className="input"
            value={reason}
            required
            maxLength={500}
            placeholder="Why they're rejected"
            aria-label="Reason for rejecting"
            onChange={(event) => setReason(event.target.value)}
          />
          <button
            type="submit"
            className="button danger"
            disabled={busy || !reason.trim()}
          >
            Reject {selected.size}
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
      {message && <p className="muted">{message}</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <ul className="list">
        {approval.items.map((item) => (
          <li
            key={item.product_id}
            className={item.state === "rejected" ? "dimmed" : undefined}
          >
            <span className="row">
              {item.state !== "rejected" && (
                <input
                  type="checkbox"
                  aria-label={`Select ${item.product_key}`}
                  checked={selected.has(item.product_id)}
                  onChange={() => toggle(item.product_id)}
                />
              )}
              <Link href={`/products/${item.product_id}`}>
                {item.product_key}
              </Link>
              <span className={`badge ${APPROVAL_BADGE[item.state]}`}>
                {APPROVAL_TEXT[item.state]}
              </span>
              {item.can_approve && <span className="badge ok">Ready</span>}
            </span>
            <span className="muted">
              {item.state === "rejected"
                ? item.rejection_reason
                : item.blockers.join(" ")}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
