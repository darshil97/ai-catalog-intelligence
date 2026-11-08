"use client";

import Link from "next/link";

import { RequireActive } from "@/components/Account";
import { NameForm } from "@/components/NameForm";
import { api, jsonBody } from "@/lib/api";
import type { Supplier, SupplierList } from "@/lib/types";
import { useApiResource } from "@/lib/useApiResource";

export default function SuppliersPage() {
  return (
    <main className="page">
      <h1>Suppliers</h1>
      <RequireActive>
        <Suppliers />
      </RequireActive>
    </main>
  );
}

function Suppliers() {
  const { data, error, reload } = useApiResource<SupplierList>("/suppliers");
  const suppliers = data?.items ?? null;

  async function create(name: string) {
    await api<Supplier>("/suppliers", jsonBody("POST", { name }));
    reload();
  }

  return (
    <div className="stack">
      <NameForm label="New supplier name" submitLabel="Add supplier" onSubmit={create} />
      {error && (
        <p className="error" role="alert">
          {error.message}
        </p>
      )}
      {suppliers === null && !error && <p className="muted">Loading…</p>}
      {suppliers?.length === 0 && (
        <p className="muted">No suppliers yet. Add your first one above.</p>
      )}
      {suppliers && suppliers.length > 0 && (
        <ul className="list">
          {suppliers.map((supplier) => (
            <li key={supplier.id}>
              <Link href={`/suppliers/${supplier.id}`}>{supplier.name}</Link>
              <span className="muted">
                {supplier.batch_count} {supplier.batch_count === 1 ? "batch" : "batches"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
