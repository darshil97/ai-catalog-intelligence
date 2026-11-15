"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiError, api, jsonBody } from "@/lib/api";
import type { BatchValidation, ProductValidation } from "@/lib/types";
import { useApiResource } from "@/lib/useApiResource";

/** "2 errors, 1 warning", or "Ready" when there's nothing to fix. */
export function checkSummary(item: ProductValidation): string {
  const parts = [
    item.errors && `${item.errors} ${item.errors === 1 ? "error" : "errors"}`,
    item.warnings &&
      `${item.warnings} ${item.warnings === 1 ? "warning" : "warnings"}`,
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : "Ready";
}

/**
 * Each product's checks under the batch's marketplace profile (D38). Checks are worked out
 * whenever this loads, so a review decision or new text shows up straight away.
 */
export function ChecksPanel({
  batchId,
  version,
}: {
  batchId: string;
  version: number;
}) {
  const checks = useApiResource<BatchValidation>(
    `/batches/${batchId}/validation`,
  );
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const { reload } = checks;

  useEffect(() => {
    if (version > 0) reload();
  }, [version, reload]);

  if (!checks.data) {
    return checks.error ? (
      <p className="error" role="alert">
        {checks.error.message}
      </p>
    ) : null;
  }
  const data = checks.data;
  if (data.items.length === 0) return null;

  async function choose(profile: string) {
    setSaving(true);
    setError(null);
    try {
      await api<BatchValidation>(
        `/batches/${batchId}/validation/profile`,
        jsonBody("PUT", { profile }),
      );
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Couldn't change it.",
      );
    } finally {
      setSaving(false);
      reload();
    }
  }

  return (
    <section className="section">
      <h2>Checks</h2>
      <div className="actions">
        <label>
          Marketplace{" "}
          <select
            value={data.profile}
            disabled={saving}
            onChange={(event) => choose(event.target.value)}
          >
            {data.profiles.map((profile) => (
              <option key={profile.code} value={profile.code}>
                {profile.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="muted">
        {data.ready} of {data.items.length}{" "}
        {data.items.length === 1 ? "product is" : "products are"} ready for{" "}
        {data.profile_name}. Errors must be fixed before approval; warnings can
        be set aside on the product page.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <ul className="list">
        {data.items.map((item) => (
          <li key={item.product_id}>
            <span>
              <Link href={`/products/${item.product_id}`}>
                {item.product_key}
              </Link>{" "}
              <span className={`badge${item.errors ? " warning" : ""}`}>
                {checkSummary(item)}
              </span>
            </span>
            {item.issues[0] && !item.issues[0].set_aside && (
              <span className="muted">{item.issues[0].message}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
