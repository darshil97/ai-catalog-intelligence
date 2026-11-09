"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

import { RequireActive } from "@/components/Account";
import { NameForm } from "@/components/NameForm";
import { ApiError, api, jsonBody } from "@/lib/api";
import type { Batch, BatchList, Supplier } from "@/lib/types";
import { useApiResource } from "@/lib/useApiResource";

export default function SupplierPage() {
  return (
    <main className="page">
      <p>
        <Link href="/suppliers">← All suppliers</Link>
      </p>
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
    if (!window.confirm("Delete this supplier?")) return;
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
        <div className="title-row">
          <h1>{data.supplier_name}</h1>
          <button
            type="button"
            className="button"
            onClick={() => setRenaming(true)}
          >
            Rename
          </button>
          <button type="button" className="button danger" onClick={remove}>
            Delete
          </button>
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      <h2>Batches</h2>
      <NameForm
        label="New batch name"
        submitLabel="Add batch"
        onSubmit={createBatch}
      />
      {data.items.length === 0 ? (
        <p className="muted">
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

function BatchRow({ batch, onChange }: { batch: Batch; onChange: () => void }) {
  const [renaming, setRenaming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function rename(name: string) {
    await api<Batch>(`/batches/${batch.id}`, jsonBody("PATCH", { name }));
    setRenaming(false);
    onChange();
  }

  async function remove() {
    if (!window.confirm(`Delete batch "${batch.name}"?`)) return;
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
        <Link href={`/batches/${batch.id}`}>{batch.name}</Link>{" "}
        <span className="badge">{batch.status}</span>
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
        <button type="button" className="button link danger" onClick={remove}>
          Delete
        </button>
      </span>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </li>
  );
}
