"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

import { RequireActive } from "@/components/Account";
import { ConfirmButton } from "@/components/ConfirmButton";
import { NameForm } from "@/components/NameForm";
import { ApiError, api, jsonBody } from "@/lib/api";
import type { Batch, BatchList, Supplier } from "@/lib/types";
import { useApiResource } from "@/lib/useApiResource";

export default function SupplierPage() {
  return (
    <main className="page">
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link href="/suppliers">Suppliers</Link>
      </nav>
      <RequireActive>
        <SupplierDetail />
      </RequireActive>
    </main>
  );
}

function SupplierDetail() {
  const { supplierId } = useParams<{ supplierId: string }>();
  const router = useRouter();
  const {
    data,
    error: loadError,
    reload,
  } = useApiResource<BatchList>(`/suppliers/${supplierId}/batches`);
  const [actionError, setActionError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState(false);
  const error =
    actionError ??
    (loadError?.status === 404
      ? "This supplier doesn't exist, or isn't yours."
      : (loadError?.message ?? null));

  async function rename(name: string) {
    await api<Supplier>(
      `/suppliers/${supplierId}`,
      jsonBody("PATCH", { name }),
    );
    setRenaming(false);
    reload();
  }

  async function remove() {
    try {
      await api<void>(`/suppliers/${supplierId}`, { method: "DELETE" });
      router.push("/suppliers");
    } catch (caught) {
      setActionError(
        caught instanceof ApiError
          ? caught.message
          : "Couldn't delete this supplier.",
      );
    }
  }

  async function createBatch(name: string) {
    await api<Batch>(
      `/suppliers/${supplierId}/batches`,
      jsonBody("POST", { name }),
    );
    reload();
  }

  if (!data) {
    return error ? (
      <p className="error" role="alert">
        {error}
      </p>
    ) : (
      <p className="muted">Loading…</p>
    );
  }

  return (
    <div className="stack">
      {renaming ? (
        <NameForm
          label="Supplier name"
          submitLabel="Save"
          initial={data.supplier_name}
          onSubmit={rename}
          onCancel={() => setRenaming(false)}
        />
      ) : (
        <div className="page-head">
          <div>
            <h1>{data.supplier_name}</h1>
            <p className="sub">
              {data.items.length} {data.items.length === 1 ? "batch" : "batches"}
            </p>
          </div>
          <div className="row">
            <button
              type="button"
              className="button"
              onClick={() => setRenaming(true)}
            >
              Rename
            </button>
            <ConfirmButton
              className="button danger"
              label="Delete"
              question="Delete this supplier?"
              confirmLabel="Delete"
              onConfirm={remove}
            />
          </div>
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      <div className="card">
        <h2>Start a batch</h2>
        <p className="muted">
          A batch holds one delivery of files, such as &ldquo;Spring price
          list&rdquo;.
        </p>
        <NameForm
          label="Batch name"
          submitLabel="Add batch"
          onSubmit={createBatch}
        />
      </div>
      <h2>Batches</h2>
      {data.items.length === 0 ? (
        <p className="empty">
          No batches yet. A batch holds the files you upload together.
        </p>
      ) : (
        <ul className="list">
          {data.items.map((batch) => (
            <BatchRow key={batch.id} batch={batch} onChange={reload} />
          ))}
        </ul>
      )}
    </div>
  );
}

const BATCH_TEXT: Record<string, string> = {
  draft: "Draft",
  queued: "Queued",
  processing: "Processing",
  completed: "Processed",
  failed: "Failed",
};
const BATCH_BADGE: Record<string, string> = {
  queued: "info",
  processing: "info",
  completed: "ok",
  failed: "error",
};

function BatchRow({ batch, onChange }: { batch: Batch; onChange: () => void }) {
  const [renaming, setRenaming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function rename(name: string) {
    await api<Batch>(`/batches/${batch.id}`, jsonBody("PATCH", { name }));
    setRenaming(false);
    onChange();
  }

  async function remove() {
    try {
      await api<void>(`/batches/${batch.id}`, { method: "DELETE" });
      onChange();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Couldn't delete this batch.",
      );
    }
  }

  if (renaming) {
    return (
      <li>
        <NameForm
          label="Batch name"
          submitLabel="Save"
          initial={batch.name}
          onSubmit={rename}
          onCancel={() => setRenaming(false)}
        />
      </li>
    );
  }

  return (
    <li>
      <span>
        <Link href={`/batches/${batch.id}`}>
          <strong>{batch.name}</strong>
        </Link>{" "}
        <span className={`badge ${BATCH_BADGE[batch.status] ?? ""}`}>
          {BATCH_TEXT[batch.status] ?? batch.status}
        </span>
      </span>
      <span className="muted">
        Deleted on {new Date(batch.expires_at).toLocaleDateString()}
      </span>
      <span className="actions">
        <button
          type="button"
          className="button link"
          onClick={() => setRenaming(true)}
        >
          Rename
        </button>
        <ConfirmButton
          className="button link danger"
          label="Delete"
          question={`Delete batch "${batch.name}" and its files?`}
          confirmLabel="Delete"
          onConfirm={remove}
        />
      </span>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </li>
  );
}
